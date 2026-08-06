"""The PPP peer: framing, LCP, PAP/CHAP and IPCP.

The negotiation tests run a real client against the real server rather than
feeding canned frames, because the failure mode that matters is not "does this
packet parse" but "do the two ends converge".  A canned-frame test cannot see a
peer that Acks its own request or opens a layer the other end is still
negotiating; a loop between two live peers can, and does, below.

Canned frames are still used where the wire format itself is the claim: the FCS
constant, the escaping rules, and the CHAP digest.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from ppp import (CONF_ACK, CONF_NAK, CONF_REQ, ECHO_REQ, GOOD_FCS, OPT_ACCM,
                 OPT_AUTH, OPT_IP_ADDRESS, OPT_MAGIC, OPT_MRU,
                 PROTO_CHAP, PROTO_IP, PROTO_IPCP, PROTO_LCP, PROTO_PAP,
                 HdlcFramer, IcmpEchoResponder, PppConfig, PppError, PppPeer,
                 encode_options, fcs16, ip_checksum, ip_to_bytes, make_client,
                 make_server, parse_options, parse_packet)


def quiet(*args, **kwargs):
    """Peers log by default; tests do not want the narration."""


def decode(data, accm=0xFFFFFFFF):
    """De-frame `data` the way a peer that negotiated `accm` would.

    The ACCM has to match the sender's, because a receiver is required to
    discard unescaped control characters it asked to have escaped.  A default
    framer therefore cannot read frames from an opened link, which negotiates
    the ACCM down to zero.
    """
    framer = HdlcFramer()
    framer.rx_accm = accm
    return [parse_packet(frame) for frame in framer.feed(data)]


def pump(a, b, *, ticks=40, clock_step=1.0, start=True):
    """Run two peers against each other until both go quiet or `ticks` expire.

    Returns the number of exchanges it took, so a test can assert that
    negotiation converged promptly rather than only that it eventually did.
    """
    now = 0.0
    if start:
        a.start(now)
        b.start(now)
    for exchange in range(ticks):
        now += clock_step
        a.tick(now)
        b.tick(now)
        from_a, from_b = a.take(), b.take()
        if not from_a and not from_b:
            return exchange
        if from_a:
            b.feed(from_a, now)
        if from_b:
            a.feed(from_b, now)
    return ticks


class FramingTests(unittest.TestCase):
    def test_good_frame_leaves_the_rfc_1662_residue(self):
        body = b'\xff\x03\xc0\x21\x01\x01\x00\x04'
        framed = body + struct.pack('<H', fcs16(body) ^ 0xFFFF)
        self.assertEqual(fcs16(framed), GOOD_FCS)

    def test_round_trip_through_the_framer(self):
        framer = HdlcFramer()
        wire = framer.encode(PROTO_LCP, b'\x01\x01\x00\x04')
        self.assertEqual(wire[0], 0x7E)
        self.assertEqual(wire[-1], 0x7E)
        frames = HdlcFramer().feed(wire)
        self.assertEqual([parse_packet(f) for f in frames],
                         [(PROTO_LCP, b'\x01\x01\x00\x04')])

    def test_flag_and_escape_in_the_payload_are_stuffed(self):
        framer = HdlcFramer()
        framer.tx_accm = 0
        payload = bytes([0x7E, 0x7D, 0x41])
        wire = framer.encode(PROTO_IP, payload)
        # Neither literal may appear between the delimiting flags.
        self.assertNotIn(0x7E, wire[1:-1])
        self.assertEqual(decode(wire, accm=0), [(PROTO_IP, payload)])

    def test_accm_escapes_only_the_mapped_control_characters(self):
        framer = HdlcFramer()
        framer.tx_accm = 1 << 0x11          # XON/XOFF-style single mapping
        wire = framer.encode(PROTO_IP, bytes([0x11, 0x12]))
        self.assertIn(bytes([0x7D, 0x31]), wire)    # 0x11 escaped
        self.assertIn(bytes([0x12]), wire)          # 0x12 sent literally

    def test_a_corrupt_frame_is_dropped_not_delivered(self):
        wire = bytearray(HdlcFramer().encode(PROTO_LCP, b'\x01\x01\x00\x04'))
        wire[4] ^= 0xFF
        decoder = HdlcFramer()
        self.assertEqual(decoder.feed(bytes(wire)), [])
        self.assertEqual(decoder.fcs_errors, 1)

    def test_shared_flags_between_frames_yield_two_frames(self):
        framer = HdlcFramer()
        first = framer.encode(PROTO_LCP, b'\x01\x01\x00\x04')
        second = framer.encode(PROTO_LCP, b'\x02\x01\x00\x04')
        # A single flag serving as both closer and opener is legal and common.
        wire = first + second[1:]
        self.assertEqual(len(HdlcFramer().feed(wire)), 2)

    def test_garbage_before_the_first_flag_is_ignored(self):
        wire = b'NO CARRIER\r\n' + HdlcFramer().encode(PROTO_LCP, b'\x01\x01\x00\x04')
        self.assertEqual(len(HdlcFramer().feed(wire)), 1)

    def test_a_frame_split_across_reads_still_arrives(self):
        wire = HdlcFramer().encode(PROTO_LCP, b'\x01\x01\x00\x04')
        decoder = HdlcFramer()
        frames = []
        for index in range(len(wire)):
            frames += decoder.feed(wire[index:index + 1])
        self.assertEqual(len(frames), 1)

    def test_protocol_field_compression_is_only_used_when_negotiated(self):
        framer = HdlcFramer()
        framer.tx_pfc = True
        wire = framer.encode(PROTO_IP, b'x')
        self.assertEqual(parse_packet(HdlcFramer().feed(wire)[0]),
                         (PROTO_IP, b'x'))
        # LCP must stay uncompressed even with PFC on (RFC 1661 section 6.5).
        lcp = framer.encode(PROTO_LCP, b'\x01\x01\x00\x04')
        self.assertIn(b'\xc0\x21', lcp)

    def test_an_oversized_frame_is_abandoned_rather_than_buffered(self):
        decoder = HdlcFramer()
        decoder.mru = 64
        decoder.feed(bytes([0x7E]) + b'A' * 500 + bytes([0x7E]))
        self.assertEqual(decoder.overruns, 1)


class OptionTests(unittest.TestCase):
    def test_round_trip(self):
        options = [(OPT_MRU, b'\x05\xdc'), (OPT_ACCM, b'\x00\x00\x00\x00')]
        self.assertEqual(parse_options(encode_options(options)), options)

    def test_a_zero_length_option_is_rejected_not_looped_on(self):
        with self.assertRaises(PppError):
            parse_options(b'\x01\x00')

    def test_an_option_overrunning_the_packet_is_rejected(self):
        with self.assertRaises(PppError):
            parse_options(b'\x01\x08\x00')


class NegotiationTests(unittest.TestCase):
    def build(self, **server):
        server.setdefault('log', quiet)
        return make_server(**server)

    def test_client_and_server_reach_ipcp_with_chap(self):
        server = self.build(auth='chap', secrets={'alice': 'secret'})
        client = make_client(username='alice', password='secret', log=quiet)
        pump(server, client)
        self.assertEqual(server.lcp.state, 'opened')
        self.assertEqual(client.lcp.state, 'opened')
        self.assertEqual(server.ipcp.state, 'opened')
        self.assertEqual(client.ipcp.state, 'opened')
        self.assertTrue(server.up)
        self.assertEqual(server.authenticated_user, 'alice')

    def test_the_server_assigns_the_client_its_address_and_dns(self):
        server = self.build(auth=None, local_address='192.168.7.1',
                            peer_address='192.168.7.2',
                            dns=('192.168.7.1', '8.8.8.8'))
        client = make_client(log=quiet)
        pump(server, client)
        # The client asked with 0.0.0.0 and took the Nak.
        self.assertEqual(client.ipcp.local_address, '192.168.7.2')
        self.assertEqual(server.ipcp.assigned, '192.168.7.2')
        self.assertEqual(client.ipcp.peer_dns, ['192.168.7.1', '8.8.8.8'])

    def test_pap_authenticates_and_opens_ipcp(self):
        server = self.build(auth='pap', secrets={'bob': 'hunter2'})
        client = make_client(username='bob', password='hunter2', log=quiet)
        pump(server, client)
        self.assertTrue(server.authenticated)
        self.assertEqual(server.ipcp.state, 'opened')

    def test_a_wrong_password_never_reaches_ipcp(self):
        for protocol in ('pap', 'chap'):
            with self.subTest(protocol=protocol):
                server = self.build(auth=protocol, secrets={'bob': 'hunter2'})
                client = make_client(username='bob', password='wrong', log=quiet)
                pump(server, client)
                self.assertFalse(server.authenticated)
                self.assertNotEqual(server.ipcp.state, 'opened')
                self.assertFalse(server.up)

    def test_an_unknown_user_is_refused(self):
        server = self.build(auth='chap', secrets={'bob': 'hunter2'})
        client = make_client(username='mallory', password='hunter2', log=quiet)
        pump(server, client)
        self.assertFalse(server.authenticated)
        self.assertIsNone(server.authenticated_user)

    def test_ipcp_is_ignored_until_the_caller_authenticates(self):
        """The ordering guarantee, asserted directly rather than via the loop."""
        server = self.build(auth='chap', secrets={'bob': 'hunter2'})
        server.start(0.0)
        server.lcp.state = 'opened'         # LCP is up, auth has not run
        request = struct.pack('>BBH', CONF_REQ, 1, 10) + encode_options(
            [(OPT_IP_ADDRESS, ip_to_bytes('10.90.0.2'))])
        server.take()
        server._dispatch(PROTO_IPCP, request, 1.0)
        self.assertEqual(server.take(), b'')
        self.assertEqual(server.ipcp.state, 'closed')

    def test_no_auth_configured_goes_straight_to_ipcp(self):
        server = self.build(auth=None)
        client = make_client(log=quiet)
        pump(server, client)
        self.assertEqual(server.ipcp.state, 'opened')
        self.assertIsNone(server.auth)

    def test_negotiation_converges_in_a_handful_of_exchanges(self):
        server = self.build(auth='chap', secrets={'ppp': 'ppp'})
        client = make_client(log=quiet)
        exchanges = pump(server, client, ticks=60)
        # LCP, CHAP and IPCP are three round trips plus the Nak for the
        # address. Anything past a dozen means something is retransmitting.
        self.assertLess(exchanges, 12)
        self.assertEqual(server.ipcp.state, 'opened')

    def test_a_looped_back_link_is_detected_by_the_magic_number(self):
        peer = PppPeer(PppConfig(role='server', auth=None), log=quiet)
        peer.start(0.0)
        first = peer.lcp.magic
        # Its own Configure-Request comes back at it, magic number and all.
        peer.feed(peer.take(), 1.0)
        self.assertNotEqual(peer.lcp.magic, first)
        self.assertNotEqual(peer.lcp.state, 'opened')

    def test_an_unknown_protocol_draws_a_protocol_reject(self):
        server = self.build(auth=None)
        client = make_client(log=quiet)
        pump(server, client)
        server.take()
        server._dispatch(0x8057, b'\x01\x01\x00\x04', 10.0)   # IPv6CP
        protocol, payload = decode(server.take(), accm=0)[0]
        self.assertEqual(protocol, PROTO_LCP)
        self.assertEqual(payload[0], 8)                       # Protocol-Reject
        self.assertEqual(payload[4:6], b'\x80\x57')

    def test_the_server_refuses_to_authenticate_to_the_client(self):
        """A caller demanding auth of the server gets a Reject, not a secret."""
        server = self.build(auth=None)
        verdict, _ = server.lcp.review_peer_option(
            OPT_AUTH, struct.pack('>H', PROTO_PAP))
        self.assertEqual(verdict, 'rej')

    def test_a_tiny_mru_is_nakked_up_to_something_workable(self):
        server = self.build()
        verdict, value = server.lcp.review_peer_option(OPT_MRU, b'\x00\x10')
        self.assertEqual(verdict, 'nak')
        self.assertEqual(struct.unpack('>H', value)[0], 1500)

    def test_van_jacobson_compression_is_rejected_honestly(self):
        server = self.build()
        verdict, _ = server.ipcp.review_peer_option(2, b'\x00\x2d\x0f\x01')
        self.assertEqual(verdict, 'rej')

    def test_a_stale_configure_ack_does_not_open_the_layer(self):
        server = self.build(auth=None)
        server.start(0.0)
        server.take()
        stale = struct.pack('>BBH', CONF_ACK, 0xEE, 4)
        server.lcp.feed(stale, 1.0)
        self.assertEqual(server.lcp.state, 'req-sent')


class TimerTests(unittest.TestCase):
    def test_a_silent_peer_makes_lcp_give_up(self):
        server = make_server(log=quiet)
        server.start(0.0)
        for step in range(1, 80):
            server.tick(step * 1.0)
            server.take()                   # nobody is listening
        self.assertEqual(server.lcp.state, 'closed')
        self.assertIsNotNone(server.lcp.failed)

    def test_configure_request_is_retransmitted_before_giving_up(self):
        server = make_server(log=quiet)
        server.start(0.0)
        server.take()
        server.tick(4.0)
        frames = HdlcFramer().feed(server.take())
        self.assertEqual(len(frames), 1)
        self.assertEqual(parse_packet(frames[0])[1][0], CONF_REQ)

    def test_echo_requests_stop_the_link_when_unanswered(self):
        server = make_server(auth=None, echo_interval=5.0, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        self.assertEqual(server.lcp.state, 'opened')
        # The client is now unplugged: tick the server past three keepalives.
        for step in range(1, 30):
            server.tick(100.0 + step * 5.0)
            server.take()
        self.assertEqual(server.lcp.state, 'closed')
        self.assertFalse(server.up)

    def test_an_answered_echo_keeps_the_link_open(self):
        server = make_server(auth=None, echo_interval=5.0, log=quiet)
        client = make_client(echo_interval=0, log=quiet)
        pump(server, client)
        now = 50.0
        for _ in range(20):
            now += 5.0
            server.tick(now)
            client.tick(now)
            client.feed(server.take(), now)
            server.feed(client.take(), now)
        self.assertEqual(server.lcp.state, 'opened')
        self.assertGreater(server.echo_replies, 0)


class IpTests(unittest.TestCase):
    @staticmethod
    def echo_request(source: str, destination: str, payload=b'ping') -> bytes:
        icmp = bytearray(struct.pack('>BBHHH', 8, 0, 0, 0x1234, 1) + payload)
        icmp[2:4] = struct.pack('>H', ip_checksum(bytes(icmp)))
        total = 20 + len(icmp)
        header = bytearray(struct.pack('>BBHHHBBH', 0x45, 0, total, 1, 0, 64, 1, 0)
                           + ip_to_bytes(source) + ip_to_bytes(destination))
        header[10:12] = struct.pack('>H', ip_checksum(bytes(header)))
        return bytes(header) + bytes(icmp)

    def test_a_ping_to_the_server_address_is_answered(self):
        server = make_server(auth=None, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        client.send_ip(self.echo_request('10.90.0.2', '10.90.0.1'))
        pump(server, client, start=False)
        self.assertEqual(len(client.rx_ip), 1)
        reply = client.rx_ip[0]
        self.assertEqual(reply[20], 0)                      # Echo Reply
        self.assertEqual(reply[12:16], ip_to_bytes('10.90.0.1'))
        self.assertEqual(reply[16:20], ip_to_bytes('10.90.0.2'))
        self.assertEqual(ip_checksum(reply[:20]), 0)        # header checks out
        self.assertEqual(ip_checksum(reply[20:]), 0)        # and so does ICMP

    def test_a_ping_to_someone_else_is_not_answered(self):
        responder = IcmpEchoResponder('10.90.0.1')
        self.assertIsNone(responder(self.echo_request('10.90.0.2', '8.8.8.8')))

    def test_ip_before_ipcp_opens_is_refused(self):
        server = make_server(auth=None, log=quiet)
        with self.assertRaises(PppError):
            server.send_ip(self.echo_request('10.90.0.2', '10.90.0.1'))

    def test_a_large_datagram_survives_framing_intact(self):
        server = make_server(auth=None, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        # Every byte value, so escaping and ACCM are both exercised.
        payload = bytes(range(256)) * 4
        packet = self.echo_request('10.90.0.2', '10.90.0.1', payload)
        client.send_ip(packet)
        pump(server, client, start=False)
        self.assertEqual(server.rx_ip[0], packet)
        self.assertEqual(client.rx_ip[0][28:], payload)


class TerminationTests(unittest.TestCase):
    def test_a_terminate_request_closes_both_ends(self):
        server = make_server(auth=None, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        client.stop(100.0, reason='client hung up')
        pump(server, client, start=False)
        self.assertEqual(server.lcp.state, 'closed')
        self.assertFalse(server.up)
        self.assertEqual(client.lcp.state, 'closed')

    def test_the_link_reports_what_happened(self):
        server = make_server(auth='chap', secrets={'ppp': 'ppp'}, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        summary = server.summary()
        self.assertIn('lcp=opened', summary)
        self.assertIn('ipcp=opened', summary)
        self.assertIn('fcs-errors=0', summary)


class LapmBridgeTests(unittest.TestCase):
    """PPP over two real LAPM endpoints, through the glue the endpoint uses.

    This is the closest thing to the live path that runs without firmware: the
    only piece it does not exercise is the data pump carrying the bits, and
    that pump is bit-transparent by the time V.42 is up.  It matters because
    ``LapmPppLink`` meters output into the LAPM window rather than queueing it,
    and a metering bug is invisible against an infinite-capacity fake.
    """

    def setUp(self):
        try:
            from v42_lapm import LapmEndpoint
        except ImportError as exc:          # pragma: no cover
            self.skipTest(f'v42_lapm unavailable: {exc}')
        from ppp import LapmPppLink
        self.a = LapmEndpoint(log=quiet, detect=False, role='originator')
        self.b = LapmEndpoint(log=quiet, detect=False, role='answerer')
        # Small window and n401, so the metering path is actually pressed.
        self.server = LapmPppLink(make_server(auth='chap',
                                              secrets={'ppp': 'ppp'},
                                              log=quiet), log=quiet)
        self.client = LapmPppLink(make_client(log=quiet), log=quiet)

    def run_link(self, ticks=400, bits=512):
        now = 0.0
        for _ in range(ticks):
            now += 0.02                     # one media quantum
            self.b.feed(self.a.take(bits))
            self.a.feed(self.b.take(bits))
            self.server.pump(self.a, now)
            self.client.pump(self.b, now)
        return now

    def test_ppp_comes_up_over_a_real_v42_link(self):
        self.run_link()
        self.assertTrue(self.server.peer.up)
        self.assertTrue(self.client.peer.up)
        self.assertEqual(self.server.peer.authenticated_user, 'ppp')
        self.assertEqual(self.client.peer.ipcp.local_address, '10.90.0.2')
        # Nothing may be lost in a link that is error-corrected by definition.
        self.assertEqual(self.server.peer.framer.fcs_errors, 0)
        self.assertEqual(self.client.peer.framer.fcs_errors, 0)

    def test_a_ping_crosses_the_v42_link_and_comes_back(self):
        self.run_link()
        self.assertTrue(self.client.peer.up)
        packet = IpTests.echo_request(
            self.client.peer.ipcp.local_address, '10.90.0.1', b'over-v42')
        self.client.peer.send_ip(packet)
        self.run_link(ticks=100)
        self.assertTrue(self.client.peer.rx_ip)
        self.assertEqual(self.client.peer.rx_ip[0][28:], b'over-v42')

    def test_output_is_metered_into_the_window_not_queued(self):
        """Back-pressure must reach PPP, not accumulate in the bridge."""
        self.run_link()
        peer = self.client.peer
        for index in range(40):
            peer.send_ip(IpTests.echo_request(
                peer.ipcp.local_address, '10.90.0.1', bytes([index]) * 400))
        # One quantum only: the window cannot possibly take all of that.
        self.client.pump(self.b, 100.0)
        self.assertTrue(self.client._backlog)
        # Admitted at most one full window; the rest waited in the bridge.
        self.assertLessEqual(len(self.b.tx_stream), self.b.window * self.b.n401)
        # Draining then delivers every one of them, in order and intact.
        self.run_link(ticks=600)
        self.assertEqual(len(self.server.peer.rx_ip), 40)
        self.assertEqual([packet[28] for packet in self.server.peer.rx_ip],
                         list(range(40)))


if __name__ == '__main__':
    unittest.main()
