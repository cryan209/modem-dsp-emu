#!/usr/bin/env python3
"""Run the PPP server on a pseudo-terminal, with no emulator underneath.

The point is to have somewhere to aim a client while the modem path is not in
the picture.  A failure here is a PPP failure; a failure over `--ppp` on the
SIP endpoint but not here is a data-path failure.  Keeping those two apart is
worth the sixty lines, and it is also the only way to test a client on a
machine with no firmware images.

    tools/ppp_serve.py
    [ppp-serve] listening on /dev/ttys012

Then point a client at that path.  With the system pppd, as the *client*:

    sudo pppd /dev/ttys012 115200 noauth nodetach user ppp

and once IPCP is up, `ping 10.90.0.1` is answered by this process.  Nothing is
routed anywhere else: IP terminates here by design.

`--tcp PORT` serves the same thing over a socket instead, for a client that
speaks to a port rather than a tty.
"""
from __future__ import annotations

import argparse
import errno
import os
import pty
import selectors
import socket
import time
import tty

from ppp import PppConfig, PppPeer


def serve(read, write, config: PppConfig, *, poll: float = 0.02) -> None:
    """Pump one PPP peer until the transport closes or the link goes down.

    `read` returns bytes (b'' when there is nothing right now, None at EOF) and
    `write` takes bytes.  Keeping the transport behind two callables is what
    lets a PTY and a socket share the loop.
    """
    peer = PppPeer(config)
    started = time.monotonic()
    peer.start(0.0)
    while True:
        now = time.monotonic() - started
        data = read()
        if data is None:
            print('[ppp-serve] the client closed the connection')
            return
        if data:
            peer.feed(data, now)
        peer.tick(now)
        out = peer.take()
        if out:
            write(out)
        if peer.lcp.state == 'closed' and peer.lcp.failed:
            print(f'[ppp-serve] link down: {peer.lcp.failed}')
            print(f'[ppp-serve] {peer.summary()}')
            return
        time.sleep(poll)


def serve_pty(config: PppConfig) -> None:
    master, slave = pty.openpty()
    # Raw, because PPP frames are binary and any line discipline in the way
    # would rewrite 0x0D and eat the frame it belongs to.
    tty.setraw(slave)
    os.set_blocking(master, False)
    print(f'[ppp-serve] listening on {os.ttyname(slave)}')

    def read():
        try:
            return os.read(master, 4096)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EIO):
                # EIO here means nobody has the slave open yet, which is the
                # normal state before a client attaches, not an error.
                return b''
            raise

    def write(data):
        try:
            os.write(master, data)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EIO):
                raise

    try:
        serve(read, write, config)
    finally:
        os.close(master)
        os.close(slave)


def serve_tcp(config: PppConfig, port: int, host: str = '127.0.0.1') -> None:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    print(f'[ppp-serve] listening on {host}:{port}')
    while True:
        client, address = listener.accept()
        print(f'[ppp-serve] client from {address[0]}:{address[1]}')
        client.setblocking(False)
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ)

        def read():
            if not selector.select(0):
                return b''
            try:
                data = client.recv(4096)
            except BlockingIOError:
                return b''
            return data if data else None

        try:
            serve(read, lambda data: client.sendall(data), config)
        except (ConnectionResetError, BrokenPipeError):
            print('[ppp-serve] the client went away')
        finally:
            selector.close()
            client.close()
        # One caller at a time, then wait for the next: this is a dial-in
        # server, and a modem has one line.


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tcp', type=int, metavar='PORT',
                    help='serve on a TCP port instead of a pseudo-terminal')
    ap.add_argument('--auth', choices=('none', 'pap', 'chap'), default='chap',
                    help='what to demand of the caller (default chap)')
    ap.add_argument('--user', default='ppp')
    ap.add_argument('--password', default='ppp')
    ap.add_argument('--local', default='10.90.0.1', metavar='IP')
    ap.add_argument('--peer', default='10.90.0.2', metavar='IP')
    ap.add_argument('--dns', default='', metavar='IP[,IP]',
                    help='DNS servers to offer (default: the local address)')
    ap.add_argument('--echo-interval', type=float, default=20.0,
                    help='LCP keepalive period in seconds, 0 to disable '
                         '(default 20)')
    args = ap.parse_args()

    dns = [part.strip() for part in args.dns.split(',') if part.strip()]
    dns = (dns + dns)[:2] if dns else [args.local, args.local]
    config = PppConfig(role='server', local_address=args.local,
                       peer_address=args.peer, dns=tuple(dns),
                       auth=None if args.auth == 'none' else args.auth,
                       secrets={args.user: args.password},
                       echo_interval=args.echo_interval)
    if args.auth != 'none':
        print(f'[ppp-serve] {args.auth.upper()}: user {args.user!r}')
    print(f'[ppp-serve] {args.local} -> {args.peer}; '
          f'{args.local} answers ping once IPCP is up')
    try:
        if args.tcp:
            serve_tcp(config, args.tcp)
        else:
            serve_pty(config)
    except KeyboardInterrupt:
        print('\n[ppp-serve] stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
