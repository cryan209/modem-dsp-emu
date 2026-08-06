#!/usr/bin/env python3
"""A tun device, so PPP clients reach the host's network instead of stopping here.

Without this, IP terminates in the PPP process: `rx_ip` collects datagrams and
the ICMP responder answers pings, which proves the link works and gets a client
no further.  A tun device hands those datagrams to the kernel, which routes them
like any other traffic, and carries the replies back.

Two things have to be true for a client to reach anything, and only the first is
this module's job:

1. **The packets must reach the kernel and come back.** That is the tun device,
   the addresses on it, and a route covering the address pool. `TunDevice` does
   all of it and undoes it on close.
2. **The kernel must be willing to forward and translate them.** That is IP
   forwarding and NAT, which are system-wide settings this module will not
   change behind anyone's back. `nat_commands()` prints exactly what is needed;
   `--nat` on ppp_serve.py runs it and reverts on exit.

Root is required either way -- creating a utun, running ifconfig and adding a
route all need it -- and this module says so plainly rather than failing deep in
an ioctl.

macOS and Linux take completely different routes to the same object. macOS has
no /dev/net/tun: a utun is a socket in the PF_SYSTEM domain, and every packet on
it carries a 4-byte address-family header that Linux's IFF_NO_PI does not.  Both
are implemented here; only the macOS path has been exercised, since that is what
this repo runs on.
"""
from __future__ import annotations

import errno
import fcntl
import os
import platform
import socket
import struct
import subprocess

# macOS: the kernel control that vends utun interfaces.
UTUN_CONTROL_NAME = b'com.apple.net.utun_control'
UTUN_OPT_IFNAME = 2
# _IOWR('N', 3, struct ctl_info), where ctl_info is 4 + 96 bytes.
CTLIOCGINFO = 0xC0644E03
AF_SYS_CONTROL = 2

# Linux: /dev/net/tun and the TUNSETIFF ioctl.
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000

# The 4-byte header macOS puts in front of every utun packet.
AF_INET_HEADER = struct.pack('>I', socket.AF_INET)


class TunError(RuntimeError):
    """The tun device could not be created or configured."""


def _run(command: list[str], *, check: bool = True) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise TunError(f'{" ".join(command)} failed: '
                       f'{result.stderr.strip() or result.stdout.strip()}')
    return result.stdout


class TunDevice:
    """A tun interface carrying raw IPv4, with the addressing it needs.

    Reads and writes are whole IP packets with no platform header: the utun
    address family prefix is added and stripped here, so callers see the same
    bytes on both systems and the same bytes PPP carries.
    """

    def __init__(self, *, name: str = '', mtu: int = 1500) -> None:
        self.requested_name = name
        self.mtu = mtu
        # set_mtu() never exceeds this, so one client with a small MRU cannot
        # ratchet the interface down for every caller after it.
        self.max_mtu = mtu
        self.name = ''
        self.fd = -1
        self.socket = None
        self.platform = platform.system()
        self._prefixed = self.platform == 'Darwin'
        self._routes: list[list[str]] = []
        self.rx_packets = 0
        self.tx_packets = 0
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.dropped_oversize = 0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        if os.geteuid() != 0:
            raise TunError(
                'a tun device needs root: creating the interface, running '
                'ifconfig and adding a route all require it. Re-run under '
                'sudo, or leave the tun off and IP will terminate in the '
                'process as before')
        if self.platform == 'Darwin':
            self._open_utun()
        elif self.platform == 'Linux':
            self._open_linux()
        else:
            raise TunError(f'no tun support for {self.platform}')

    def _open_utun(self) -> None:
        """Open a utun through the PF_SYSTEM kernel control."""
        control = socket.socket(socket.PF_SYSTEM, socket.SOCK_DGRAM,
                                socket.SYSPROTO_CONTROL)
        try:
            info = struct.pack('I96s', 0, UTUN_CONTROL_NAME)
            info = fcntl.ioctl(control, CTLIOCGINFO, info)
            control_id = struct.unpack('I96s', info)[0]
            # Unit 0 means "any free one"; the interface is utun(unit - 1).
            # Asking for a specific unit fails if a VPN already holds it, and
            # on a Mac several are normally in use.
            unit = 0
            if self.requested_name.startswith('utun'):
                suffix = self.requested_name[4:]
                if suffix.isdigit():
                    unit = int(suffix) + 1
            control.connect((control_id, unit))
            self.name = control.getsockopt(socket.SYSPROTO_CONTROL,
                                           UTUN_OPT_IFNAME, 256).rstrip(b'\0')
            self.name = self.name.decode()
        except OSError as exc:
            control.close()
            raise TunError(f'could not open a utun device: {exc}') from exc
        control.setblocking(False)
        self.socket = control
        self.fd = control.fileno()

    def _open_linux(self) -> None:
        try:
            self.fd = os.open('/dev/net/tun', os.O_RDWR)
        except OSError as exc:
            raise TunError(f'could not open /dev/net/tun: {exc}') from exc
        name = (self.requested_name or 'ppp%d').encode()
        request = struct.pack('16sH', name, IFF_TUN | IFF_NO_PI)
        try:
            request = fcntl.ioctl(self.fd, TUNSETIFF, request)
        except OSError as exc:
            os.close(self.fd)
            self.fd = -1
            raise TunError(f'TUNSETIFF failed: {exc}') from exc
        self.name = request[:16].rstrip(b'\0').decode()
        os.set_blocking(self.fd, False)

    def configure(self, local: str, peer: str, routes=()) -> None:
        """Address the interface and route `routes` down it.

        The point-to-point peer address is a formality with a pool behind it --
        one interface cannot have a peer per client -- so the pool prefix is
        routed to the interface instead, and that is what actually makes an
        assigned client reachable.
        """
        if not self.name:
            raise TunError('configure() before open()')
        if self.platform == 'Darwin':
            _run(['ifconfig', self.name, 'inet', local, peer,
                  'mtu', str(self.mtu), 'up'])
        else:
            _run(['ip', 'link', 'set', self.name, 'mtu', str(self.mtu), 'up'])
            _run(['ip', 'addr', 'add', f'{local}/32', 'peer', peer,
                  'dev', self.name])
        for destination in routes:
            self.add_route(destination)

    def add_route(self, destination: str) -> None:
        if self.platform == 'Darwin':
            # -net with an interface route: the pool has no single next hop,
            # every address in it is on the far side of this interface.
            command = ['route', '-q', 'add', '-net', destination,
                       '-interface', self.name]
        else:
            command = ['ip', 'route', 'add', destination, 'dev', self.name]
        _run(command)
        self._routes.append([destination])

    def set_mtu(self, mtu: int) -> None:
        """Match the interface to what the peer said it can receive.

        Setting this from the negotiated MRU is what keeps the kernel from
        handing down packets the client would have to drop: it fragments or
        signals PMTU on its own once the interface agrees with the link.
        """
        mtu = min(mtu, self.max_mtu)
        if mtu == self.mtu or not self.name:
            return
        self.mtu = mtu
        if self.platform == 'Darwin':
            _run(['ifconfig', self.name, 'mtu', str(mtu)], check=False)
        else:
            _run(['ip', 'link', 'set', self.name, 'mtu', str(mtu)],
                 check=False)

    def close(self) -> None:
        for route in self._routes:
            if self.platform == 'Darwin':
                _run(['route', '-q', 'delete', '-net', route[0],
                      '-interface', self.name], check=False)
            else:
                _run(['ip', 'route', 'del', route[0], 'dev', self.name],
                     check=False)
        self._routes.clear()
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        elif self.fd >= 0:
            os.close(self.fd)
        self.fd = -1
        # The interface goes away with the descriptor on both systems; there
        # is nothing to tear down by name.

    # -- packets -------------------------------------------------------------

    def read_packets(self, limit: int = 32) -> list[bytes]:
        """Whatever the kernel has queued, up to `limit` packets."""
        packets = []
        for _ in range(limit):
            try:
                data = (self.socket.recv(self.mtu + 4) if self.socket is not None
                        else os.read(self.fd, self.mtu + 4))
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                raise
            if not data:
                break
            if self._prefixed:
                if len(data) <= 4 or data[:4] != AF_INET_HEADER:
                    # IPv6 and anything else the interface picks up. Dropping
                    # is right: PPP here negotiated IPCP only, so there is no
                    # channel to carry it.
                    continue
                data = data[4:]
            self.rx_packets += 1
            self.rx_bytes += len(data)
            packets.append(data)
        return packets

    def write_packet(self, packet: bytes) -> bool:
        """Hand one IP packet to the kernel. False if it could not be taken."""
        if not packet:
            return False
        if len(packet) > self.mtu:
            # The client sent something larger than the interface admits.
            # Dropping beats a partial write, which would corrupt the stream.
            self.dropped_oversize += 1
            return False
        data = AF_INET_HEADER + packet if self._prefixed else packet
        try:
            if self.socket is not None:
                self.socket.send(data)
            else:
                os.write(self.fd, data)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS):
                return False
            raise
        self.tx_packets += 1
        self.tx_bytes += len(packet)
        return True

    def summary(self) -> str:
        return (f'{self.name or "(unopened)"} mtu={self.mtu} '
                f'in={self.rx_packets}p/{self.rx_bytes}B '
                f'out={self.tx_packets}p/{self.tx_bytes}B '
                f'oversize-dropped={self.dropped_oversize}')


class TunBridge:
    """Plug a ``TunDevice`` into a ``PppPeer`` as its network handler.

    The peer calls ``deliver()`` for every datagram a client sends and
    ``poll()`` once per tick for anything coming back.  Keeping the tun behind
    this pair rather than in the peer is what lets the tests substitute a fake
    device and exercise the whole path without root.
    """

    def __init__(self, device, log=print) -> None:
        self.device = device
        self.log = log
        self.to_network = 0
        self.from_network = 0
        self.refused = 0

    def deliver(self, packet: bytes) -> None:
        if self.device.write_packet(packet):
            self.to_network += 1
        else:
            # No queue here on purpose. The kernel's own buffer is the queue,
            # and a second one in userspace would add latency to a path whose
            # round-trip time is the measurement this rig exists to make.
            self.refused += 1

    def poll(self) -> list[bytes]:
        packets = self.device.read_packets()
        self.from_network += len(packets)
        return packets

    def summary(self) -> str:
        return (f'{self.device.summary()}; refused={self.refused}')


def nat_commands(interface: str, pool: str, uplink: str = '',
                 system: str = '') -> list[list[str]]:
    """The forwarding and NAT a client needs to reach anything off this host.

    Returned rather than run, because these are system-wide settings: IP
    forwarding affects every interface, and on macOS loading a pf ruleset
    touches the firewall.  The caller decides, and ppp_serve.py only does it
    behind an explicit --nat.

    `system` names the platform to generate for, defaulting to this one. It is
    a parameter so the branch that cannot run here can still be tested here.
    """
    if (system or platform.system()) == 'Darwin':
        return [['sysctl', '-w', 'net.inet.ip.forwarding=1'],
                ['pfctl', '-e']]
    return [['sysctl', '-w', 'net.ipv4.ip_forward=1'],
            ['iptables', '-t', 'nat', '-A', 'POSTROUTING',
             '-s', pool, '-o', uplink or 'eth0', '-j', 'MASQUERADE'],
            ['iptables', '-A', 'FORWARD', '-i', interface, '-s', pool,
             '-j', 'ACCEPT'],
            ['iptables', '-A', 'FORWARD', '-o', interface, '-d', pool,
             '-j', 'ACCEPT']]


def default_uplink() -> str:
    """The interface the host's own default route uses, for NAT."""
    try:
        if platform.system() == 'Darwin':
            output = _run(['route', '-n', 'get', 'default'], check=False)
            for line in output.splitlines():
                if 'interface:' in line:
                    return line.split(':', 1)[1].strip()
        else:
            output = _run(['ip', 'route', 'show', 'default'], check=False)
            fields = output.split()
            if 'dev' in fields:
                return fields[fields.index('dev') + 1]
    except (TunError, OSError, ValueError):
        pass
    return ''


def pf_nat_rule(pool: str, uplink: str) -> str:
    """The pf rule macOS needs, for the caller to write into an anchor.

    macOS has no iptables and pf will not take a rule from the command line, so
    this is handed back as text to be loaded with `pfctl -f`.
    """
    return f'nat on {uplink} from {pool} to any -> ({uplink})\n'
