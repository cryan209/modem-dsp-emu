"""The userspace NAT: TCP, UDP and ICMP terminated here and re-originated.

These tests drive real sockets -- a loopback TCP server, a loopback UDP echo
server, a ping to 127.0.0.1 -- because the whole claim of this module is that
client flows become ordinary host sockets.  A test against a mocked socket
would assert the shape of the code and nothing about whether the connection
happens.

The client side is hand-built segments rather than a second TCP stack. That is
deliberate: it is the only way to assert exact sequence numbers, flags and
checksums, and to construct the awkward cases (a zero window, a RST, a FIN on
truncated data) that a real stack will not produce on demand.
"""
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from usernet import (ACK, FIN, PROTO_ICMP, PROTO_TCP, PROTO_UDP, PSH, RST,
                     SYN, UserNetwork, build_ipv4, build_tcp, build_udp,
                     checksum, parse_ipv4, parse_tcp, tcp_mss_option)

CLIENT = bytes((100, 64, 0, 2))
LOOPBACK = bytes((127, 0, 0, 1))


def quiet(*args, **kwargs):
    pass


def ip(address: str) -> bytes:
    return bytes(int(part) for part in address.split('.'))


class Harness:
    """A UserNetwork plus the bookkeeping to speak TCP at it as a client."""

    def __init__(self, client_port=40000, mtu=1500):
        self.net = UserNetwork(mtu=mtu, log=quiet)
        self.client_port = client_port
        self.seq = 1000
        self.ack = 0
        self.window = 65535

    def close(self):
        self.net.close()

    def send(self, remote, remote_port, flags, payload=b'', options=b'',
             window=None, seq=None):
        segment = build_tcp(CLIENT, remote, self.client_port, remote_port,
                            self.seq if seq is None else seq, self.ack, flags,
                            self.window if window is None else window,
                            payload, options)
        self.net.deliver(build_ipv4(CLIENT, remote, PROTO_TCP, segment))
        if seq is None:
            self.seq = (self.seq + len(payload)
                        + (1 if flags & (SYN | FIN) else 0)) & 0xFFFFFFFF

    def pump(self, rounds=40, delay=0.01, until=None):
        """Poll until `until` is satisfied, collecting every packet emitted."""
        collected = []
        for _ in range(rounds):
            for packet in self.net.poll():
                collected.append(packet)
            if until is not None and until(collected):
                break
            time.sleep(delay)
        return collected

    def segments(self, packets):
        out = []
        for packet in packets:
            parsed = parse_ipv4(packet)
            if parsed and parsed[2] == PROTO_TCP:
                out.append(parse_tcp(parsed[3]))
        return out

    def handshake(self, remote, remote_port):
        """SYN, take the SYN-ACK, ACK it. Returns the server's segment."""
        self.send(remote, remote_port, SYN,
                  options=struct.pack('>BBH', 2, 4, 1400))
        packets = self.pump(until=lambda c: self.segments(c))
        found = self.segments(packets)
        assert found, 'no SYN-ACK'
        synack = found[0]
        assert synack['flags'] & SYN and synack['flags'] & ACK, synack['flags']
        self.ack = (synack['seq'] + 1) & 0xFFFFFFFF
        self.send(remote, remote_port, ACK)
        return synack


class EchoServer:
    """A loopback TCP server that echoes until the client half-closes."""

    def __init__(self, on_connect=None):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received = bytearray()
        self.on_connect = on_connect
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        with connection:
            if self.on_connect is not None:
                self.on_connect(connection)
                return
            while True:
                try:
                    data = connection.recv(4096)
                except OSError:
                    return
                if not data:
                    connection.shutdown(socket.SHUT_WR)
                    return
                self.received += data
                connection.sendall(data)

    def close(self):
        self.listener.close()


class HeaderTests(unittest.TestCase):
    def test_ipv4_round_trip(self):
        packet = build_ipv4(CLIENT, LOOPBACK, PROTO_TCP, b'body', 7)
        self.assertEqual(checksum(packet[:20]), 0)
        self.assertEqual(parse_ipv4(packet),
                         (CLIENT, LOOPBACK, PROTO_TCP, b'body'))

    def test_a_fragment_is_rejected_rather_than_misread(self):
        packet = bytearray(build_ipv4(CLIENT, LOOPBACK, PROTO_TCP, b'body'))
        packet[6:8] = struct.pack('>H', 0x2000)      # more-fragments
        self.assertIsNone(parse_ipv4(bytes(packet)))

    def test_a_truncated_packet_is_rejected(self):
        packet = build_ipv4(CLIENT, LOOPBACK, PROTO_TCP, b'body' * 10)
        self.assertIsNone(parse_ipv4(packet[:24]))

    def test_tcp_checksum_covers_the_pseudo_header(self):
        segment = build_tcp(CLIENT, LOOPBACK, 1, 2, 3, 4, ACK, 100, b'xy')
        pseudo = CLIENT + LOOPBACK + struct.pack('>BBH', 0, PROTO_TCP,
                                                 len(segment))
        self.assertEqual(checksum(pseudo + segment), 0)
        # The same segment between different addresses must not verify.
        other = ip('10.0.0.1') + LOOPBACK + struct.pack('>BBH', 0, PROTO_TCP,
                                                        len(segment))
        self.assertNotEqual(checksum(other + segment), 0)

    def test_udp_checksum_is_never_transmitted_as_zero(self):
        # Zero means "not computed" in UDP, so it must encode as all-ones.
        for length in range(1, 40):
            datagram = build_udp(CLIENT, LOOPBACK, 1, 2, b'\x00' * length)
            self.assertNotEqual(datagram[6:8], b'\x00\x00')

    def test_mss_option_is_found_past_padding(self):
        self.assertEqual(tcp_mss_option(b'\x01\x01' + struct.pack('>BBH', 2, 4, 1460)),
                         1460)
        self.assertIsNone(tcp_mss_option(b'\x00\x00'))
        self.assertIsNone(tcp_mss_option(b'\x02'))          # truncated
        self.assertIsNone(tcp_mss_option(b'\x08\x02'))      # not MSS


class TcpTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.addCleanup(self.harness.close)

    def test_a_client_connection_becomes_a_real_socket(self):
        server = EchoServer()
        self.addCleanup(server.close)
        synack = self.harness.handshake(LOOPBACK, server.port)
        self.assertEqual(synack['sport'], server.port)
        self.assertEqual(synack['dport'], self.harness.client_port)
        # The advertised MSS is what *we* can receive, so it is the link's
        # 1460 rather than an echo of the client's 1400.
        self.assertEqual(tcp_mss_option(synack['options']), 1460)
        # What we send is bounded by the client's number instead.
        flow = next(iter(self.harness.net.tcp.values()))
        self.assertEqual(flow.mss, 1400)

    def test_data_crosses_to_the_server_and_the_reply_comes_back(self):
        server = EchoServer()
        self.addCleanup(server.close)
        self.harness.handshake(LOOPBACK, server.port)
        self.harness.send(LOOPBACK, server.port, PSH | ACK, b'hello world')
        packets = self.harness.pump(
            until=lambda c: any(s['payload'] == b'hello world'
                                for s in self.harness.segments(c)))
        payloads = [s['payload'] for s in self.harness.segments(packets)
                    if s['payload']]
        self.assertIn(b'hello world', payloads)
        self.assertEqual(bytes(server.received), b'hello world')

    def test_the_reply_segments_carry_a_valid_checksum(self):
        server = EchoServer()
        self.addCleanup(server.close)
        self.harness.handshake(LOOPBACK, server.port)
        self.harness.send(LOOPBACK, server.port, PSH | ACK, b'check me')
        packets = self.harness.pump(
            until=lambda c: any(s['payload'] for s in self.harness.segments(c)))
        checked = 0
        for packet in packets:
            source, destination, protocol, segment = parse_ipv4(packet)
            self.assertEqual(checksum(packet[:20]), 0)
            pseudo = source + destination + struct.pack('>BBH', 0, protocol,
                                                        len(segment))
            self.assertEqual(checksum(pseudo + segment), 0)
            checked += 1
        self.assertTrue(checked)

    def test_a_refused_port_is_reset_not_left_hanging(self):
        # Bind and close, so the port is almost certainly free and refusing.
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        self.harness.send(LOOPBACK, port, SYN)
        packets = self.harness.pump(
            until=lambda c: any(s['flags'] & RST
                                for s in self.harness.segments(c)))
        self.assertTrue(any(s['flags'] & RST
                            for s in self.harness.segments(packets)))

    def test_a_segment_for_an_unknown_flow_draws_a_reset(self):
        self.harness.send(LOOPBACK, 9, PSH | ACK, b'stray')
        segments = self.harness.segments(self.harness.pump(rounds=3))
        self.assertTrue(segments)
        self.assertTrue(segments[0]['flags'] & RST)

    def test_a_reset_is_never_answered_with_a_reset(self):
        """Otherwise two ends can trade resets forever."""
        self.harness.send(LOOPBACK, 9, RST)
        self.assertEqual(self.harness.segments(self.harness.pump(rounds=3)), [])

    def test_the_client_fin_half_closes_and_the_server_fin_comes_back(self):
        server = EchoServer()
        self.addCleanup(server.close)
        self.harness.handshake(LOOPBACK, server.port)
        self.harness.send(LOOPBACK, server.port, PSH | ACK, b'bye')
        self.harness.pump(rounds=10)
        self.harness.send(LOOPBACK, server.port, FIN | ACK)
        packets = self.harness.pump(
            until=lambda c: any(s['flags'] & FIN
                                for s in self.harness.segments(c)))
        self.assertTrue(any(s['flags'] & FIN
                            for s in self.harness.segments(packets)))

    def test_a_closed_flow_is_reaped(self):
        server = EchoServer()
        self.addCleanup(server.close)
        self.harness.handshake(LOOPBACK, server.port)
        self.assertEqual(len(self.harness.net.tcp), 1)
        self.harness.send(LOOPBACK, server.port, RST)
        self.harness.pump(rounds=3)
        self.assertEqual(len(self.harness.net.tcp), 0)

    def test_a_large_transfer_arrives_whole_and_in_order(self):
        """The segmentation, window and ack path, end to end."""
        payload = bytes(range(256)) * 200            # 51,200 bytes
        server = EchoServer(on_connect=lambda c: c.sendall(payload))
        self.addCleanup(server.close)
        self.harness.handshake(LOOPBACK, server.port)
        received = bytearray()
        expected_seq = None
        for _ in range(400):
            for segment in self.harness.segments(self.harness.pump(rounds=1)):
                if not segment['payload']:
                    continue
                if expected_seq is None:
                    expected_seq = segment['seq']
                # In-order and contiguous: no gaps, no overlap.
                self.assertEqual(segment['seq'], expected_seq)
                received += segment['payload']
                expected_seq = (expected_seq
                                + len(segment['payload'])) & 0xFFFFFFFF
                # Acknowledge, so the window keeps opening.
                self.harness.ack = expected_seq
                self.harness.send(LOOPBACK, server.port, ACK)
            if len(received) >= len(payload):
                break
        self.assertEqual(bytes(received), payload)

    def test_segments_never_exceed_the_mss(self):
        payload = b'x' * 20000
        server = EchoServer(on_connect=lambda c: c.sendall(payload))
        self.addCleanup(server.close)
        harness = Harness(client_port=40001, mtu=576)
        self.addCleanup(harness.close)
        harness.handshake(LOOPBACK, server.port)
        seen = []
        for _ in range(60):
            for segment in harness.segments(harness.pump(rounds=1)):
                if segment['payload']:
                    seen.append(len(segment['payload']))
                    harness.ack = (segment['seq']
                                   + len(segment['payload'])) & 0xFFFFFFFF
                    harness.send(LOOPBACK, server.port, ACK)
            if sum(seen) >= len(payload):
                break
        self.assertTrue(seen)
        # mtu 576 - 40 = 536, and the whole point is not to exceed the link.
        self.assertLessEqual(max(seen), 536)

    def test_a_zero_window_stops_the_sender(self):
        payload = b'y' * 40000
        server = EchoServer(on_connect=lambda c: c.sendall(payload))
        self.addCleanup(server.close)
        self.harness.handshake(LOOPBACK, server.port)
        self.harness.send(LOOPBACK, server.port, ACK, window=0)
        sent = 0
        for _ in range(20):
            for segment in self.harness.segments(self.harness.pump(rounds=1)):
                sent += len(segment['payload'])
        # Nothing may be sent into a closed window beyond what was already in
        # flight when it closed.
        self.assertEqual(sent, 0)


class UdpTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.addCleanup(self.harness.close)

    def test_a_datagram_round_trips_through_a_real_socket(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0))
        self.addCleanup(server.close)
        port = server.getsockname()[1]

        def echo():
            data, address = server.recvfrom(4096)
            server.sendto(b'reply:' + data, address)

        thread = threading.Thread(target=echo, daemon=True)
        thread.start()

        datagram = build_udp(CLIENT, LOOPBACK, 5555, port, b'query')
        self.harness.net.deliver(build_ipv4(CLIENT, LOOPBACK, PROTO_UDP,
                                            datagram))
        packets = self.harness.pump(rounds=50)
        replies = []
        for packet in packets:
            source, destination, protocol, payload = parse_ipv4(packet)
            if protocol == PROTO_UDP:
                self.assertEqual(destination, CLIENT)
                replies.append(payload[8:])
                # The ports must be swapped back for the client to match it.
                sport, dport = struct.unpack('>HH', payload[:4])
                self.assertEqual((sport, dport), (port, 5555))
        self.assertEqual(replies, [b'reply:query'])

    def test_a_short_datagram_is_refused_not_unpacked(self):
        self.harness.net.deliver(build_ipv4(CLIENT, LOOPBACK, PROTO_UDP,
                                            b'\x00\x01'))
        self.assertEqual(self.harness.net.refused, 1)
        self.assertEqual(len(self.harness.net.udp), 0)

    def test_the_mapping_is_reused_for_the_same_four_tuple(self):
        for _ in range(3):
            datagram = build_udp(CLIENT, LOOPBACK, 5555, 9, b'x')
            self.harness.net.deliver(build_ipv4(CLIENT, LOOPBACK, PROTO_UDP,
                                                datagram))
        self.assertEqual(len(self.harness.net.udp), 1)


class IcmpTests(unittest.TestCase):
    def setUp(self):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                  socket.IPPROTO_ICMP)
        except OSError as exc:
            self.skipTest(f'no unprivileged ICMP socket here: {exc}')
        probe.close()
        self.harness = Harness()
        self.addCleanup(self.harness.close)

    @staticmethod
    def echo_request(identifier, sequence, payload=b'ping'):
        icmp = bytearray(struct.pack('>BBHHH', 8, 0, 0, identifier, sequence)
                         + payload)
        icmp[2:4] = struct.pack('>H', checksum(bytes(icmp)))
        return bytes(icmp)

    def test_a_ping_to_a_real_address_is_answered_by_that_address(self):
        request = self.echo_request(0x4242, 1, b'through-the-nat')
        self.harness.net.deliver(build_ipv4(CLIENT, LOOPBACK, PROTO_ICMP,
                                            request))
        packets = self.harness.pump(rounds=100)
        replies = [parse_ipv4(p) for p in packets]
        replies = [r for r in replies if r and r[2] == PROTO_ICMP]
        self.assertTrue(replies, 'no ICMP echo reply came back')
        source, destination, _protocol, payload = replies[0]
        self.assertEqual(destination, CLIENT)
        self.assertEqual(payload[0], 0)                     # Echo Reply
        # The identifier must be the client's, whatever the kernel did to it.
        self.assertEqual(struct.unpack('>H', payload[4:6])[0], 0x4242)
        self.assertEqual(payload[8:], b'through-the-nat')
        self.assertEqual(checksum(payload), 0)

    def test_anything_but_an_echo_request_is_refused(self):
        # Timestamp request: it needs a raw socket, which is the privilege
        # this module exists to avoid.
        self.harness.net.deliver(build_ipv4(CLIENT, LOOPBACK, PROTO_ICMP,
                                            b'\x0d\x00\x00\x00' + b'\x00' * 8))
        self.assertEqual(self.harness.net.refused, 1)


class UnsupportedTests(unittest.TestCase):
    def test_an_unsupported_protocol_is_counted_not_silently_dropped(self):
        net = UserNetwork(log=quiet)
        self.addCleanup(net.close)
        net.deliver(build_ipv4(CLIENT, LOOPBACK, 47, b'gre payload'))
        self.assertEqual(net.refused, 1)
        self.assertIn('unsupported=1', net.summary())

    def test_a_non_ip_packet_is_refused(self):
        net = UserNetwork(log=quiet)
        self.addCleanup(net.close)
        net.deliver(b'not an ip packet at all')
        self.assertEqual(net.refused, 1)


if __name__ == '__main__':
    unittest.main()
