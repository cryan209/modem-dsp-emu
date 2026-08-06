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

and once IPCP is up, `ping 100.64.0.1` is answered by this process.  Nothing is
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

from ppp import AddressPool, PppConfig, PppPeer


def serve(read, write, config: PppConfig, *, poll: float = 0.02,
          network=None) -> None:
    """Pump one PPP peer until the transport closes or the link goes down.

    `read` returns bytes (b'' when there is nothing right now, None at EOF) and
    `write` takes bytes.  Keeping the transport behind two callables is what
    lets a PTY and a socket share the loop.

    With `network` attached the poll interval matters for more than the link:
    it is the floor on the round-trip time of everything the client sends, so
    it is deliberately short.
    """
    peer = PppPeer(config)
    if network is not None:
        peer.attach_network(network)
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
            if network is not None:
                print(f'[ppp-serve] {network.summary()}')
            return
        # Sleep only when both directions were idle. Under load this spins
        # through as fast as the transport allows, which is what keeps the
        # poll interval off the round-trip time.
        if not data and not out:
            time.sleep(poll)


def serve_pty(config: PppConfig, network=None) -> None:
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
        serve(read, write, config, network=network)
    finally:
        os.close(master)
        os.close(slave)


def serve_tcp(config: PppConfig, port: int, host: str = '127.0.0.1',
              pool: AddressPool | None = None, network=None) -> None:
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

        assigned = None
        session = config
        if pool is not None:
            import dataclasses
            assigned = pool.allocate()
            session = dataclasses.replace(config, peer_address=assigned)
            print(f'[ppp-serve] assigning {assigned}')
        try:
            serve(read, lambda data: client.sendall(data), session,
                  network=network)
        except (ConnectionResetError, BrokenPipeError):
            print('[ppp-serve] the client went away')
        finally:
            if assigned is not None:
                pool.release(assigned)
            selector.close()
            client.close()
        # One caller at a time, then wait for the next: this is a dial-in
        # server, and a modem has one line.


def enable_nat(interface: str, pool: str, uplink: str = ''):
    """Turn on forwarding and NAT the pool onto the host's uplink.

    Every command is printed before it runs.  These are system-wide settings --
    forwarding affects every interface, and the pf rule touches the firewall --
    so the whole point is that nothing here happens silently, and that what was
    changed can be put back.  Returns the state needed to revert.
    """
    import platform

    from tun import _run, default_uplink, nat_commands, pf_nat_rule

    uplink = uplink or default_uplink()
    if not uplink:
        raise SystemExit('[ppp-serve] --nat needs an uplink interface and the '
                         'default route did not name one; pass --uplink')
    darwin = platform.system() == 'Darwin'
    previous = _run(['sysctl', '-n',
                     'net.inet.ip.forwarding' if darwin else
                     'net.ipv4.ip_forward'], check=False).strip()
    state = {'uplink': uplink, 'forwarding': previous, 'anchor': None,
             'darwin': darwin, 'pool': pool, 'interface': interface}
    for command in nat_commands(interface, pool, uplink):
        print(f'[ppp-serve] nat: {" ".join(command)}')
        _run(command, check=False)
    if darwin:
        # pf takes rules from a file, not the command line.
        path = '/tmp/ppp-serve-nat.conf'
        with open(path, 'w') as handle:
            handle.write(pf_nat_rule(pool, uplink))
        print(f'[ppp-serve] nat: pfctl -f {path} '
              f'({pf_nat_rule(pool, uplink).strip()})')
        _run(['pfctl', '-f', path], check=False)
        state['anchor'] = path
    print(f'[ppp-serve] NAT is on: {pool} -> {uplink}. '
          'It will be reverted when this exits')
    return state


def disable_nat(state) -> None:
    """Put back what enable_nat() changed."""
    import platform

    from tun import _run
    if state is None:
        return
    darwin = state['darwin']
    if state['forwarding'] in ('0', '1'):
        key = ('net.inet.ip.forwarding' if darwin else 'net.ipv4.ip_forward')
        _run(['sysctl', '-w', f'{key}={state["forwarding"]}'], check=False)
    if darwin:
        # Reload the system ruleset, dropping ours.
        _run(['pfctl', '-f', '/etc/pf.conf'], check=False)
        if state['anchor']:
            try:
                os.unlink(state['anchor'])
            except OSError:
                pass
    else:
        _run(['iptables', '-t', 'nat', '-D', 'POSTROUTING', '-s',
              state['pool'], '-o', state['uplink'], '-j', 'MASQUERADE'],
             check=False)
        _run(['iptables', '-D', 'FORWARD', '-i', state['interface'],
              '-s', state['pool'], '-j', 'ACCEPT'], check=False)
        _run(['iptables', '-D', 'FORWARD', '-o', state['interface'],
              '-d', state['pool'], '-j', 'ACCEPT'], check=False)
    print('[ppp-serve] NAT reverted')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tcp', type=int, metavar='PORT',
                    help='serve on a TCP port instead of a pseudo-terminal')
    ap.add_argument('--auth', choices=('none', 'pap', 'chap'), default='chap',
                    help='what to demand of the caller (default chap)')
    ap.add_argument('--user', default='ppp')
    ap.add_argument('--password', default='ppp')
    ap.add_argument('--local', default='100.64.0.1', metavar='IP',
                    help="this end's address (default 100.64.0.1)")
    ap.add_argument('--pool', default='100.64.0.0/10', metavar='CIDR',
                    help='the prefix clients are assigned from (default '
                         '100.64.0.0/10, RFC 6598 shared address space)')
    ap.add_argument('--peer', default='', metavar='IP',
                    help='assign this exact address instead of allocating '
                         'from --pool')
    ap.add_argument('--dns', default='', metavar='IP[,IP]',
                    help='DNS servers to offer (default: the local address)')
    ap.add_argument('--echo-interval', type=float, default=20.0,
                    help='LCP keepalive period in seconds, 0 to disable '
                         '(default 20)')
    ap.add_argument('--tun', action='store_true',
                    help='route the client through a kernel tun device rather '
                         'than the userspace NAT. Needs root, and needs --nat '
                         'to reach anything off this host, but it carries '
                         'every protocol and lets the host reach the client, '
                         'which the userspace NAT cannot')
    ap.add_argument('--no-network', action='store_true',
                    help='do not give the client a network at all: IP '
                         'terminates in this process and only ping to the '
                         'local address is answered. This is the old '
                         'behaviour, kept for isolating link problems from '
                         'network ones')
    ap.add_argument('--tun-name', default='', metavar='NAME',
                    help='ask for a specific interface (utunN, or a Linux '
                         'name); default is the first free one')
    ap.add_argument('--nat', action='store_true',
                    help='also enable IP forwarding and NAT the pool onto the '
                         'default route, which is what lets a client reach '
                         'anything off this host. These are system-wide '
                         'settings; they are printed before being applied and '
                         'reverted on exit')
    ap.add_argument('--uplink', default='', metavar='IFACE',
                    help='the interface to NAT onto (default: whichever the '
                         "host's own default route uses)")
    args = ap.parse_args()

    if args.nat and not args.tun:
        ap.error('--nat without --tun has nothing to translate: the client '
                 'never reaches the kernel. The userspace NAT needs no '
                 'forwarding rules at all')
    if args.tun and args.no_network:
        ap.error('--tun and --no-network contradict each other')

    dns = [part.strip() for part in args.dns.split(',') if part.strip()]
    dns = (dns + dns)[:2] if dns else [args.local, args.local]
    pool = None
    peer = args.peer
    if not peer:
        try:
            # Reserving the local address keeps the pool from issuing this end
            # to a client, which would be a silent conflict on the link.
            pool = AddressPool(args.pool, reserve=(args.local,))
        except ValueError as exc:
            ap.error(f'--pool: {exc}')
        # One tty is one client, so the PTY path takes its address now and
        # keeps it. The TCP path allocates per connection instead, in
        # serve_tcp, and must not consume one here as well.
        peer = args.local if args.tcp else pool.allocate()
    config = PppConfig(role='server', local_address=args.local,
                       peer_address=peer, dns=tuple(dns),
                       auth=None if args.auth == 'none' else args.auth,
                       secrets={args.user: args.password},
                       echo_interval=args.echo_interval)
    if args.auth != 'none':
        print(f'[ppp-serve] {args.auth.upper()}: user {args.user!r}')
    print(f'[ppp-serve] {args.local} -> '
          f'{peer if args.peer else args.pool}')

    network = None
    device = None
    nat = None
    if args.tun:
        from tun import TunBridge, TunDevice, TunError
        device = TunDevice(name=args.tun_name)
        try:
            device.open()
            # Route the whole pool down the interface. The point-to-point peer
            # address is a formality: one interface cannot have a peer per
            # client, and it is this route that makes an assigned address
            # reachable.
            # The TCP path has not allocated an address yet -- it does that per
            # connection -- so preview one rather than consuming a pool entry
            # on an address that is only there to satisfy ifconfig.
            iface_peer = peer if peer != args.local else pool.preview()
            device.configure(args.local, iface_peer, routes=(args.pool,))
        except TunError as exc:
            device.close()
            # A missing privilege or a busy interface is a setup problem, not
            # a defect; the message says what to do about it.
            raise SystemExit(f'[ppp-serve] {exc}') from None
        except Exception:
            device.close()
            raise
        print(f'[ppp-serve] {device.name} up: {args.local} -> {iface_peer}, '
              f'{args.pool} routed to it')
        network = TunBridge(device)
        if args.nat:
            nat = enable_nat(device.name, args.pool, args.uplink)
    elif args.no_network:
        print(f'[ppp-serve] no network: IP terminates here and {args.local} '
              'answers ping')
    else:
        from usernet import UserNetwork
        network = UserNetwork(log=print)
        print('[ppp-serve] userspace NAT: TCP, UDP and ICMP echo are '
              're-originated as host sockets. No root, nothing routed')

    try:
        if args.tcp:
            # The PTY case keeps the single address taken above: one tty is
            # one client, and reallocating per attach would move the address
            # under a client that merely reopened the device.
            serve_tcp(config, args.tcp, pool=pool, network=network)
        else:
            serve_pty(config, network=network)
    except KeyboardInterrupt:
        print('\n[ppp-serve] stopped')
    finally:
        # Reverting NAT before closing the device: the rules name the
        # interface, and removing them after it has gone leaves the system
        # ruleset referring to something that no longer exists.
        disable_nat(nat)
        if device is not None:
            print(f'[ppp-serve] {device.summary()}')
            device.close()
        elif network is not None:
            print(f'[ppp-serve] {network.summary()}')
            network.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
