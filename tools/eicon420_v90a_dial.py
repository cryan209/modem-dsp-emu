#!/usr/bin/env python3
"""Place one V.90A call from a Divatty port and log every byte with timestamps.

usage: eicon420_v90a_dial.py TTY CONTROLLER NUMBER HOLD_SECONDS LOGFILE

AT&F5 (async modem) is required to originate; AT&F14 is an incoming-only
autodetect profile and makes every ATD return NO DIALTONE at once.
"""
import os, sys, termios, time, select

dev, ctrl, number, hold, log = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), sys.argv[5]
fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = 0; a[1] = 0
a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL | termios.CRTSCTS
a[3] = 0; a[4] = a[5] = termios.B115200
a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, a)
out = open(log, 'w')
t0 = time.monotonic()

def note(tag, data):
    out.write(f'{time.monotonic()-t0:9.3f} {tag} {data!r}\n'); out.flush()
    print(f'{time.monotonic()-t0:9.3f} {tag} {data!r}', flush=True)

def read_until(tokens, timeout):
    buf = b''; end = time.monotonic() + timeout
    while time.monotonic() < end:
        if select.select([fd], [], [], 0.2)[0]:
            try: d = os.read(fd, 4096)
            except BlockingIOError: continue
            if d: note('<', d); buf += d
            for t in tokens:
                if t in buf: return buf
    return buf

def cmd(c, timeout=5):
    note('>', c); os.write(fd, (c + '\r').encode())
    r = read_until([b'OK', b'ERROR'], timeout)
    if b'OK' not in r: raise SystemExit(f'{c}: {r!r}')

for c in ('AT&F5', f'AT+iQ=o{ctrl}', 'AT+iQ=?', 'AT+iO7911', 'AT+MS=V90a,0', r'AT\N3%C1', 'ATE0V1S0=0', 'AT+MS?'):
    cmd(c)
note('>', f'ATD{number}'); os.write(fd, f'ATD{number}\r'.encode())
r = read_until([b'CONNECT', b'NO CARRIER', b'BUSY', b'NO ANSWER', b'ERROR'], 90)
if b'CONNECT' in r:
    read_until([b'Select: '], 15)
    os.write(fd, b'1\r'); note('>', '1 (echo test)')
    end = time.monotonic() + hold
    i = 0
    while time.monotonic() < end:
        os.write(fd, f'v90a probe line {i}\r'.encode()); i += 1
        read_until([b'\n'], 1.0)
    os.write(fd, b'/menu\r'); read_until([b'Select: '], 5)
    os.write(fd, b'q\r'); read_until([b'NO CARRIER'], 5)
time.sleep(1.5)
os.write(fd, b'+++'); time.sleep(1.5)
os.write(fd, b'ATH\r'); read_until([b'OK'], 5)
note('#', 'done')
