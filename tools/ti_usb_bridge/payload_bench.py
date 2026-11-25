#!/usr/bin/env python3
"""Timed, SHA-256-verified incompressible modem transfers (stdlib only)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import select
import subprocess
import time
import tty

SIZE = 256 * 1024

def payload(seed, size):
    return random.Random(seed).randbytes(size)

class Port:
    def __init__(self, path, out):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self.fd)
        self.buf = bytearray()
        self.out = out
        self.started = time.monotonic()

    def log(self, event, **fields):
        row = dict(seconds=round(time.monotonic()-self.started, 6), event=event, **fields)
        print(json.dumps(row), flush=True)
        self.out.write(json.dumps(row)+'\n'); self.out.flush()

    def send(self, data, timeout=180):
        end = time.monotonic()+timeout
        offset = 0
        while offset < len(data):
            if time.monotonic() >= end:
                raise TimeoutError('serial write')
            if select.select([], [self.fd], [], .1)[1]:
                try:
                    count = os.write(self.fd, data[offset:offset+4096])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise RuntimeError('PTY closed during write')
                offset += count

    def receive(self, count, timeout=180):
        end = time.monotonic()+timeout
        while len(self.buf) < count:
            if time.monotonic() >= end:
                raise TimeoutError(f'serial read: need {count}, have {len(self.buf)}')
            if select.select([self.fd], [], [], .1)[0]:
                try:
                    data = os.read(self.fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    raise EOFError('PTY closed')
                self.buf.extend(data)
        data = bytes(self.buf[:count]); del self.buf[:count]
        return data

    def line(self, timeout=180):
        end = time.monotonic()+timeout
        data = bytearray()
        while time.monotonic() < end:
            char = self.receive(1, max(.01, end-time.monotonic()))
            data.extend(char)
            if char == b'\n':
                return bytes(data).strip()
            if len(data) > 4096:
                raise RuntimeError('oversized control line')
        raise TimeoutError('control line')

    def until(self, tokens, timeout):
        end = time.monotonic()+timeout
        data = bytearray()
        while time.monotonic() < end:
            data.extend(self.receive(1, max(.01, end-time.monotonic())))
            if any(token in data for token in tokens):
                return bytes(data)
        raise TimeoutError(repr(bytes(data)))

    def command(self, text):
        self.send(text.encode()+b'\r')
        response = self.until((b'\r\nOK\r\n', b'\r\nERROR\r\n'), 6)
        self.log('command', command=text, response=response.decode(errors='replace'))
        if not response.endswith(b'\r\nOK\r\n'):
            raise RuntimeError(text+' rejected')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def rate(count, elapsed):
    return dict(bytes=count, elapsed_seconds=round(elapsed, 6),
                bytes_per_second=round(count/elapsed, 2),
                payload_kbit_per_second=round(count*8/elapsed/1000, 3))

def server(port, size):
    while True:
        line = port.line(100)
        port.log('answerer_result', text=line.decode(errors='replace'))
        if line.startswith(b'CONNECT'):
            break
    port.send(b'MT9234_BENCH_READY\n')
    assert port.line(30) == b'DOWNLOAD', 'download request missing'
    data = payload(9234001, size)
    sha = digest(data)
    started = time.monotonic()
    port.send(f'DATA {size} {sha}\n'.encode()+data)
    assert port.line(180) == f'DOWNLOAD_OK {sha}'.encode(), 'download verification failed'
    port.log('download_delivered_acknowledged', sha256=sha, **rate(size, time.monotonic()-started))
    port.send(b'UPLOAD_READY\n')
    fields = port.line(30).decode().split()
    assert fields[:2] == ['UPLOAD', str(size)], fields
    started = time.monotonic()
    received = port.receive(size)
    elapsed = time.monotonic()-started
    assert received == payload(9234002, size), 'upstream payload differs'
    sha = digest(received)
    assert fields[2] == sha, 'upstream digest differs'
    port.log('upload_received_verified', sha256=sha, **rate(size, elapsed))
    port.send(f'UPLOAD_OK {size} {sha}\n'.encode())
    assert port.line(30) == b'QUIT', 'quit missing'
    port.send(b'BYE\n')
    try:
        response = port.until((b'NO CARRIER',), 20)
        port.log('disconnected', text=response.decode(errors='replace'))
    except TimeoutError:
        port.log('disconnect_wait_expired')

def client(port, number, size, out):
    connected = False
    try:
        for cmd in ('ATE0', 'ATV1', 'ATM0', 'AT+MS=V90,0,0,31200,0,46666'):
            port.command(cmd)
        port.send(('ATD'+number+'\r').encode())
        response = port.until((b'CONNECT', b'NO CARRIER', b'BUSY', b'NO DIALTONE', b'ERROR'), 100)
        if not response.endswith(b'CONNECT'):
            raise RuntimeError('dial failed: '+repr(response))
        connected = True
        result_line = b'CONNECT '+port.line(10)
        port.log('connected', text=result_line.decode())
        while port.line(15) != b'MT9234_BENCH_READY':
            pass
        start = time.monotonic(); port.send(b'DOWNLOAD\n')
        header = port.line(15).decode().split()
        assert header[:2] == ['DATA', str(size)], header
        data = port.receive(size)
        elapsed = time.monotonic()-start
        sha = digest(data)
        assert sha == header[2] and data == payload(9234001, size), 'downstream payload differs'
        down = dict(sha256=sha, **rate(size, elapsed))
        port.log('download_received_verified', **down)
        port.send(f'DOWNLOAD_OK {sha}\n'.encode())
        assert port.line(15) == b'UPLOAD_READY', 'upload not ready'
        data = payload(9234002, size); sha = digest(data)
        start = time.monotonic()
        port.send(f'UPLOAD {size} {sha}\n'.encode()+data)
        ack = port.line(180)
        elapsed = time.monotonic()-start
        assert ack == f'UPLOAD_OK {size} {sha}'.encode(), ack
        up = dict(sha256=sha, **rate(size, elapsed))
        port.log('upload_delivered_acknowledged', **up)
        result = dict(size_per_direction=size, download=down, upload=up,
                      timing='download: request to final byte; upload: first write to verified peer acknowledgement')
        (out/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
        port.send(b'QUIT\n'); assert port.line(15) == b'BYE'
    finally:
        if connected:
            time.sleep(1.2); port.send(b'+++'); time.sleep(1.2)
            # Drain escape results, then explicitly hang up and confirm.
            port.buf.clear()
            while select.select([port.fd], [], [], .05)[0]:
                os.read(port.fd, 4096)
            port.command('ATH')
        else:
            port.send(b'\r')
        port.send(b'ATI11\r'); diagnostics=bytearray()
        for _ in range(6):
            reply=port.until((b'Press any key', b'\r\nOK\r\n'), 8)
            diagnostics.extend(reply)
            if reply.endswith(b'\r\nOK\r\n'):
                break
            port.send(b' ')
        (out/'ati11.txt').write_bytes(diagnostics)
        port.log('diagnostics', text=diagnostics.decode(errors='replace'))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('role', choices=('client','server'))
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--port', default='/tmp/mt9234zba')
    parser.add_argument('--number', default='2903')
    parser.add_argument('--size', type=int, default=SIZE)
    parser.add_argument('--emulator')
    args=parser.parse_args()
    assert 1 <= args.size <= 4*1024*1024
    args.out.mkdir(parents=True, exist_ok=False)
    proc=None; port=None
    with (args.out/'events.jsonl').open('w') as log:
        try:
            if args.role=='server':
                assert args.emulator
                env=dict(os.environ, SIP_FORCE_PCMU='1', ME_MODE='v90', ME_V90_UPSTREAM_MAX_BPS='26400',
                         VPCM_G711_TAP_DIR=str(args.out))
                with (args.out/'server.log').open('w') as server_log:
                    proc=subprocess.Popen([args.emulator, '--sip-server','asterisk.net.cryan.nz',
                        '--username',args.number,'--password',args.number,'--local-port','5097',
                        '--rtp-port','15320','--pty-link',args.port,'--verbose'],env=env,stdout=server_log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+15
                while not Path(args.port).exists():
                    if proc.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError('emulator startup failed')
                    time.sleep(.1)
            port=Port(args.port, log)
            if args.role=='server':
                time.sleep(1)
                for cmd in ('ATE0','AT+MS=V90','AT&A3','AT+ER=1','ATS0=1'):
                    port.command(cmd)
                port.log('ready')
                server(port,args.size)
            else:
                client(port,args.number,args.size,args.out)
        finally:
            if port is not None:
                os.close(port.fd)
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(8)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()

if __name__=='__main__':
    main()
