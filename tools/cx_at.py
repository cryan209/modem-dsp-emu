#!/usr/bin/env python3
"""Drive an external analogue modem over its serial port, to place live calls.

The handoff has referenced this tool since Session 76, but it is not in the
tree; the live-call half of the rig has to be rebuilt before any hardware test
can run.  It talks to whatever modem is on the given device with plain AT, so
it works for the CX93001 and for the two USR Couriers equally.

Device paths are not stable across reboots -- the CX93001 that Sessions 72--79
reached at /dev/cu.usbmodem246802461 now enumerates elsewhere -- so `ident`
identifies what is actually attached before anything is dialled:

    python3 tools/cx_at.py ident /dev/cu.usb*

`dial` places a call and reports the result codes as they arrive, which is what
pairs with a capture taken on the other side:

    python3 tools/cx_at.py --dev /dev/cu.usbmodem123456781 \
        --setup 'AT&F' --setup 'AT+MS=V34,0' dial 6001 --wait 75

No pyserial: termios does everything needed and keeps the tool runnable under
the same interpreter as the rest of the repo.
"""
from __future__ import annotations

import argparse
import os
import sys
import termios
import time

BAUD = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
        57600: termios.B57600, 115200: termios.B115200}

# Result codes that end a dial attempt, in the order atp.c lists them.
TERMINAL = ('CONNECT', 'NO CARRIER', 'NO DIALTONE', 'NO DIAL TONE', 'BUSY',
            'NO ANSWER', 'ERROR')


class Modem:
    def __init__(self, device: str, baud: int = 115200):
        self.device = device
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0          # raw iflag, oflag, lflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = BAUD[baud]
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def close(self) -> None:
        os.close(self.fd)

    def write(self, text: str) -> None:
        os.write(self.fd, text.encode('ascii') + b'\r')

    def read(self, seconds: float) -> str:
        deadline = time.monotonic() + seconds
        buffer = b''
        while time.monotonic() < deadline:
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                chunk = b''
            if chunk:
                buffer += chunk
            else:
                time.sleep(0.02)
        return buffer.decode('ascii', 'replace')

    def command(self, text: str, seconds: float = 1.5) -> str:
        self.write(text)
        return self.read(seconds)

    def await_result(self, seconds: float, echo: bool) -> str:
        """Read until a terminal result code arrives or the timeout expires."""
        deadline = time.monotonic() + seconds
        buffer = ''
        while time.monotonic() < deadline:
            buffer += self.read(0.25)
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip('\r \t')
                if not line:
                    continue
                if echo:
                    print(f'  [{time.strftime("%H:%M:%S")}] {line}', flush=True)
                if any(line.upper().startswith(code) for code in TERMINAL):
                    return line
        return ''


def show(text: str, prefix: str = '    ') -> None:
    for line in text.replace('\r', '\n').splitlines():
        if line.strip():
            print(prefix + line.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dev', help='serial device of the modem to drive')
    ap.add_argument('--baud', type=int, default=115200, choices=sorted(BAUD))
    ap.add_argument('--setup', action='append', default=[],
                    help='AT command to send before dialling; repeatable')
    sub = ap.add_subparsers(dest='action', required=True)

    ident = sub.add_parser('ident', help='identify attached modems, no dialling')
    ident.add_argument('devices', nargs='+')

    dial = sub.add_parser('dial', help='place a call')
    dial.add_argument('number')
    dial.add_argument('--wait', type=float, default=75.0,
                      help='seconds to stay on the call before hanging up')
    dial.add_argument('--pulse', action='store_true',
                      help='pulse dial (ATDP) instead of tone. Only for a line '
                           'that actually decodes loop disconnect; an ATA that '
                           'does not will reject every number identically, '
                           'which reads as the destination being busy')

    cmd = sub.add_parser('cmd', help='send AT commands and print the replies')
    cmd.add_argument('commands', nargs='+')

    args = ap.parse_args()

    if args.action == 'ident':
        for device in args.devices:
            print(device)
            try:
                modem = Modem(device, args.baud)
            except OSError as error:
                print(f'    unavailable: {error.strerror}')
                continue
            try:
                for probe in ('ATI3', 'ATI0'):
                    show(modem.command(probe))
            finally:
                modem.close()
        return 0

    if not args.dev:
        ap.error('--dev is required for this action')
    modem = Modem(args.dev, args.baud)
    try:
        if args.action == 'cmd':
            for text in args.commands:
                print(text)
                show(modem.command(text))
            return 0

        for text in args.setup:
            print(f'setup: {text}')
            reply = modem.command(text)
            show(reply)
            if 'ERROR' in reply.upper():
                print(f'setup command rejected: {text}', file=sys.stderr)
                return 1

        print(f'dialling {args.number} on {args.dev}', flush=True)
        # Drain anything the setup commands left behind first.  AT&F drops DTR,
        # and the NO CARRIER that follows arrives after the OK -- left in the
        # buffer it reads as this call's outcome and reports a connected call
        # as NO CARRIER.  Read until the line has been quiet for a moment, then
        # flush, and ignore any terminal code in the first two seconds: no real
        # dial on this path resolves that fast.
        while modem.read(0.4):
            pass
        termios.tcflush(modem.fd, termios.TCIFLUSH)
        # Tone by default, and explicitly rather than by relying on the modem's
        # own default: a bare ATD dials however the profile was left, and AT&F
        # restores pulse on some firmware. Into an FXS port that does not
        # decode loop disconnect the digits simply never arrive, so the gateway
        # rejects the call locally and every number -- including one that does
        # not exist -- comes back BUSY at the same speed. That looks like a
        # dead route rather than a dial-method problem, which cost a session.
        modem.write(f'ATD{"P" if args.pulse else "T"}{args.number}')
        show(modem.read(2.0), prefix='  echo: ')
        result = modem.await_result(60.0, echo=True)
        if not result.upper().startswith('CONNECT'):
            print(f'call did not connect: {result or "(timeout)"}')
            return 1

        print(f'connected; holding for {args.wait:.0f}s', flush=True)
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            text = modem.read(1.0)
            for line in text.replace('\r', '\n').splitlines():
                if line.strip():
                    print(f'  [{time.strftime("%H:%M:%S")}] {line.strip()}',
                          flush=True)
                    if line.strip().upper().startswith('NO CARRIER'):
                        print('peer dropped the call')
                        return 1
        print('hanging up')
        modem.write('+++')
        time.sleep(1.2)
        show(modem.command('ATH'))
        return 0
    finally:
        modem.close()


if __name__ == '__main__':
    raise SystemExit(main())
