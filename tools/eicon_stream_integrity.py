#!/usr/bin/env python3
"""Bounded deterministic duplex integrity probe for a physical TTY or emulator PTY.

Standard-library only so the same file runs on the card host. Serial mode
waits for an explicit ACK on stdin before hanging up after both byte counts
finish; CANCEL aborts safely during transfer. PTY mode preserves its existing
raw settings and must be armed before dialling.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import termios
import time


class Pattern:
    block_size = 16384

    def __init__(self, seed):
        self.seed = seed.encode()
        self.cache = {}

    def at(self, offset, length):
        output = bytearray()
        while length:
            block, index = divmod(offset, self.block_size)
            if block not in self.cache:
                self.cache = {block: hashlib.shake_256(
                    self.seed + block.to_bytes(8, 'big')).digest(self.block_size)}
            count = min(length, self.block_size - index)
            output.extend(self.cache[block][index:index + count])
            offset += count
            length -= count
        return bytes(output)


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def command(fd, text, timeout=5):
    os.write(fd, (text + '\r').encode())
    end = time.monotonic() + timeout
    result = bytearray()
    while time.monotonic() < end:
        if select.select([fd], [], [], .1)[0]:
            result.extend(os.read(fd, 4096))
            if b'OK' in result or b'ERROR' in result:
                break
    if b'OK' not in result:
        raise RuntimeError(f'{text}: {bytes(result)!r}')


def run(args):
    fd = os.open(args.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    serial = args.mode == 'serial'
    started = time.monotonic()
    result = dict(passed=False, size=args.size, seed=args.seed, mode=args.mode)
    tx = rx = 0
    digest = hashlib.sha256()
    try:
        if serial:
            a = termios.tcgetattr(fd)
            a[0] = a[1] = a[3] = 0
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL | termios.CRTSCTS
            a[4] = a[5] = termios.B115200
            a[6][termios.VMIN] = a[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, a)
            fcntl.ioctl(fd, termios.TIOCMBIS,
                        struct.pack('i', termios.TIOCM_DTR | termios.TIOCM_RTS))
            setup = (('AT', 'ATE0V1') if getattr(args, 'modem', 'eicon') == 'multitech'
                     else ('AT&F5', 'ATE0V1', f'AT+iQ=o{args.controller}',
                           'AT+iO7901', 'AT+MS=V90a,1', r'AT\N3%C0'))
            for text in setup:
                command(fd, text)
            os.write(fd, ('ATD' + args.number + '\r').encode())
            reply = bytearray()
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .1)[0]:
                    reply.extend(os.read(fd, 4096))
                    complete_connect = (b'CONNECT ' in reply and
                                        b'\r\n' in reply[reply.index(b'CONNECT '):])
                    if complete_connect or any(x in reply for x in
                            (b'NO CARRIER', b'NO DIALTONE', b'BUSY', b'ERROR')):
                        break
            if b'CONNECT ' not in reply:
                raise RuntimeError(f'no carrier: {bytes(reply)!r}')
            # The peer is not permitted to send until LAPM; any bytes after
            # the CONNECT line are payload and must not be discarded.
            marker = reply.index(b'CONNECT ')
            line_end = reply.find(b'\r\n', marker)
            if line_end < 0:
                raise RuntimeError('incomplete CONNECT line')
            initial = bytes(reply[line_end + 2:])
            emit('connected', reply=bytes(reply[:line_end]).decode(errors='replace').strip(),
                 seconds=time.monotonic() - started)
        else:
            initial = b''
        own = 'serial' if serial else 'pty'
        peer = 'pty' if serial else 'serial'
        source = Pattern(f'eicon-integrity-v1:{args.seed}:{own}')
        expected = Pattern(f'eicon-integrity-v1:{args.seed}:{peer}')
        data_start = time.monotonic()
        stop_at = data_start + args.duration if serial and args.duration else None
        tx_limit = None if args.duration else args.size
        rx_limit = None if args.duration else args.size
        last_progress = data_start
        emit('armed', device=args.device)
        pending = initial
        while True:
            if time.monotonic() - data_start > args.timeout:
                raise TimeoutError('duplex transfer deadline exceeded')
            if stop_at is not None and time.monotonic() >= stop_at and tx_limit is None:
                tx_limit = tx
                emit('tx_final', tx=tx)
            if pending:
                wanted = expected.at(rx, len(pending))
                if (rx_limit is not None and rx + len(pending) > rx_limit) or pending != wanted:
                    mismatch = next((i for i, pair in enumerate(zip(pending, wanted))
                                     if pair[0] != pair[1]), 0)
                    raise RuntimeError(f'payload mismatch at byte {rx + mismatch}')
                digest.update(pending)
                rx += len(pending)
                pending = b''
            if tx_limit is not None and rx_limit is not None and tx == tx_limit and rx == rx_limit:
                break
            readers = [fd] + ([sys.stdin] if serial or args.duration else [])
            writing = tx_limit is None or tx < tx_limit
            ready, writable, _ = select.select(readers, [fd] if writing else [], [], .1)
            if sys.stdin in ready:
                control = sys.stdin.readline().strip()
                if control == 'START' and args.duration and not serial:
                    stop_at = time.monotonic() + args.duration
                elif control.startswith('RX_LIMIT ') and args.duration:
                    rx_limit = int(control.split()[1])
                    if rx > rx_limit:
                        raise RuntimeError('received more bytes than peer final TX count')
                elif control != 'ACK':
                    raise RuntimeError('cancelled by controller or controller closed')
            if fd in writable:
                count = 256 if serial else 4096
                if tx_limit is not None:
                    count = min(count, tx_limit - tx)
                payload = source.at(tx, count)
                try:
                    tx += os.write(fd, payload)
                except BlockingIOError:
                    pass
            if fd in ready:
                try:
                    pending = os.read(fd, 65536)
                except BlockingIOError:
                    pass
            if time.monotonic() - last_progress >= 10:
                emit('progress', tx=tx, rx=rx, size=args.size,
                     seconds=time.monotonic() - data_start)
                last_progress = time.monotonic()
        result.update(passed=True, tx=tx, rx=rx, rx_sha256=digest.hexdigest(),
                      data_seconds=time.monotonic() - data_start)
        emit('data_pass', **result)
        if serial:
            if not select.select([sys.stdin], [], [], 20)[0] or sys.stdin.readline().strip() != 'ACK':
                raise RuntimeError('controller did not confirm peer verification')
    except Exception as error:
        result.update(passed=False, error=str(error), tx=tx, rx=rx)
    finally:
        if serial:
            fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack('i', termios.TIOCM_DTR))
        os.close(fd)
        result['total_seconds'] = time.monotonic() - started
        if args.result:
            temporary = args.result.with_suffix(args.result.suffix + '.tmp')
            temporary.write_text(json.dumps(result, indent=2) + '\n')
            temporary.replace(args.result)
        emit('result', **result)
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('serial', 'pty'), required=True)
    parser.add_argument('--device', required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--seed', required=True)
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--duration', type=float, default=0,
                        help='continuous mode: stop TX after this many seconds; '
                             'controller exchanges final byte counts on stdin')
    parser.add_argument('--controller', type=int, default=2)
    parser.add_argument('--modem', choices=('eicon', 'multitech'), default='eicon')
    parser.add_argument('--number', default='6001')
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.size <= 0 or args.timeout <= 0 or args.duration < 0:
        parser.error('size and timeout must be positive; duration nonnegative')
    if args.mode == 'serial' and subprocess.run(
            ['fuser', '-s', args.device], check=False).returncode == 0:
        parser.error('test device is already owned')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
