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
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from ppp import (AddressPool, CGNAT_PREFIX, Lcp,
                 CONF_ACK, CONF_NAK, CONF_REQ, ECHO_REQ, GOOD_FCS, OPT_ACCM,
                 OPT_AUTH, OPT_IP_ADDRESS, OPT_MAGIC, OPT_MRU,
                 PROTO_CHAP, PROTO_IP, PROTO_IPCP, PROTO_LCP, PROTO_PAP,
                 HdlcFramer, IcmpEchoResponder, PppConfig, PppError, PppPeer,
                 encode_options, fcs16, icmp_echo_request, ip_checksum,
                 ip_to_bytes, make_client, make_server, parse_icmp_echo_reply,
                 parse_options, parse_packet)


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

    def test_an_accm_change_applies_to_the_next_frame_in_the_same_read(self):
        """A peer's Configure-Ack is routinely followed, in the same read, by a
        packet already sent under the terms it just agreed. Decoding the whole
        buffer up front applies the stale ACCM to that second frame and
        silently discards it -- which cost a live call its PAP request.
        """
        sender = HdlcFramer()
        first = sender.encode(PROTO_LCP, b'\x02\x01\x00\x04')   # all escaped
        sender.tx_accm = 0                                      # now agreed
        second = sender.encode(PROTO_PAP, bytes(range(1, 24)))  # unescaped
        receiver = HdlcFramer()
        receiver.push(first + second)
        frame = receiver.next_frame()
        self.assertEqual(parse_packet(frame)[0], PROTO_LCP)
        # Acting on the first frame is what relaxes the ACCM.
        receiver.rx_accm = 0
        frame = receiver.next_frame()
        self.assertIsNotNone(frame, 'the second frame was discarded')
        self.assertEqual(parse_packet(frame),
                         (PROTO_PAP, bytes(range(1, 24))))
        self.assertEqual(receiver.fcs_errors, 0)

    def test_consumed_input_does_not_accumulate(self):
        framer = HdlcFramer()
        for _ in range(50):
            framer.feed(framer.encode(PROTO_LCP, b'\x01\x01\x00\x04'))
        self.assertLess(len(framer._input), 64)

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


class AddressPoolTests(unittest.TestCase):
    """Callers are assigned out of RFC 6598 shared space, one address each."""

    def test_the_default_prefix_is_the_rfc_6598_range(self):
        self.assertEqual(CGNAT_PREFIX, '100.64.0.0/10')
        pool = AddressPool()
        # The /10 spans 100.64.0.0 to 100.127.255.255 and nothing outside it.
        self.assertIn('100.64.0.9', pool)
        self.assertIn('100.127.255.254', pool)
        self.assertNotIn('100.63.255.255', pool)
        self.assertNotIn('100.128.0.0', pool)
        self.assertNotIn('10.0.0.1', pool)

    def test_the_default_server_address_is_in_that_range(self):
        # A server outside the pool it hands out is not wrong, but it is not
        # what the defaults should do.
        self.assertIn(PppConfig().local_address, AddressPool())
        self.assertIn(PppConfig().peer_address, AddressPool())

    def test_addresses_are_distinct_and_never_the_network_address(self):
        pool = AddressPool('100.64.0.0/29')
        issued = [pool.allocate() for _ in range(6)]
        self.assertEqual(len(set(issued)), 6)
        self.assertNotIn('100.64.0.0', issued)      # network
        self.assertNotIn('100.64.0.7', issued)      # broadcast
        self.assertEqual(issued[0], '100.64.0.1')

    def test_a_reserved_address_is_never_issued(self):
        pool = AddressPool('100.64.0.0/29', reserve=('100.64.0.1',))
        self.assertNotIn('100.64.0.1', [pool.allocate() for _ in range(5)])

    def test_exhaustion_is_an_error_rather_than_a_duplicate(self):
        pool = AddressPool('100.64.0.0/29')
        for _ in range(6):
            pool.allocate()
        with self.assertRaises(PppError):
            pool.allocate()

    def test_a_released_address_becomes_available_again(self):
        pool = AddressPool('100.64.0.0/29')
        issued = [pool.allocate() for _ in range(6)]
        pool.release(issued[2])
        self.assertEqual(len(pool), 5)
        self.assertEqual(pool.allocate(), issued[2])

    def test_releasing_twice_is_not_an_error(self):
        """Teardown paths run more than once and must stay idempotent."""
        pool = AddressPool('100.64.0.0/29')
        address = pool.allocate()
        pool.release(address)
        pool.release(address)
        pool.release('100.64.0.6')
        self.assertEqual(len(pool), 0)

    def test_the_cursor_moves_on_rather_than_reissuing_immediately(self):
        """A reconnecting client should not get an address its own stack may
        still have cached from a moment ago."""
        pool = AddressPool('100.64.0.0/24')
        first = pool.allocate()
        pool.release(first)
        self.assertNotEqual(pool.allocate(), first)

    def test_a_prefix_with_no_host_range_is_refused(self):
        for prefix in ('100.64.0.0/31', '100.64.0.1/32'):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    AddressPool(prefix)

    def test_a_host_bit_in_the_prefix_is_tolerated(self):
        # 100.64.0.5/24 means the /24 containing it, not an error.
        self.assertIn('100.64.0.200', AddressPool('100.64.0.5/24'))

    def test_a_server_assigns_a_pool_address_to_its_caller(self):
        import dataclasses
        pool = AddressPool(reserve=('100.64.0.1',))
        assigned = pool.allocate()
        server = make_server(local_address='100.64.0.1', log=quiet)
        server.config = dataclasses.replace(server.config,
                                            peer_address=assigned)
        server.peer_address = assigned
        server.ipcp.peer_address = assigned
        client = make_client(log=quiet)
        pump(server, client)
        self.assertEqual(client.ipcp.local_address, assigned)
        self.assertIn(client.ipcp.local_address, pool)
        self.assertNotEqual(client.ipcp.local_address, '100.64.0.1')


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

    def test_a_client_that_refuses_chap_is_offered_pap(self):
        """Modern Windows RAS has CHAP off by default, so this is the common
        case rather than an exceptional one."""
        server = self.build(auth='chap', secrets={'bob': 'hunter2'})
        client = make_client(username='bob', password='hunter2', log=quiet)
        # Refuse CHAP the way such a client does.
        original = client.lcp.review_peer_option

        def refuse_chap(otype, value):
            if otype == OPT_AUTH and struct.unpack('>H', value[:2])[0] == PROTO_CHAP:
                return 'rej', value
            return original(otype, value)

        client.lcp.review_peer_option = refuse_chap
        pump(server, client)
        self.assertEqual(server.lcp.require_auth, 'pap')
        self.assertTrue(server.authenticated)
        self.assertEqual(server.ipcp.state, 'opened')

    def test_a_client_that_refuses_every_protocol_is_told_why(self):
        server = self.build(auth='chap', secrets={'bob': 'hunter2'})
        client = make_client(username='bob', password='hunter2', log=quiet)
        client.lcp.review_peer_option = lambda otype, value: (
            ('rej', value) if otype == OPT_AUTH
            else Lcp.review_peer_option(client.lcp, otype, value))
        pump(server, client)
        self.assertNotEqual(server.ipcp.state, 'opened')
        self.assertIn('--ppp-auth none', server.lcp.failed)

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
            [(OPT_IP_ADDRESS, ip_to_bytes('100.64.0.2'))])
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
        client.send_ip(self.echo_request('100.64.0.2', '100.64.0.1'))
        pump(server, client, start=False)
        self.assertEqual(len(client.rx_ip), 1)
        reply = client.rx_ip[0]
        self.assertEqual(reply[20], 0)                      # Echo Reply
        self.assertEqual(reply[12:16], ip_to_bytes('100.64.0.1'))
        self.assertEqual(reply[16:20], ip_to_bytes('100.64.0.2'))
        self.assertEqual(ip_checksum(reply[:20]), 0)        # header checks out
        self.assertEqual(ip_checksum(reply[20:]), 0)        # and so does ICMP

    def test_the_originator_builds_a_request_the_responder_answers(self):
        # --ppp-ping's two halves against the responder, so the instrument is
        # known good before a link failure can be blamed on it.
        server = make_server(auth=None, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        client.send_ip(icmp_echo_request('100.64.0.2', '100.64.0.1',
                                         sequence=7))
        pump(server, client, start=False)
        self.assertEqual(parse_icmp_echo_reply(client.rx_ip[0]), (0x1234, 7))

    def test_a_request_is_not_mistaken_for_a_reply(self):
        request = icmp_echo_request('100.64.0.2', '100.64.0.1')
        self.assertIsNone(parse_icmp_echo_reply(request))
        # Nor is anything that is not ICMP at all.
        self.assertIsNone(parse_icmp_echo_reply(b'\x45\x00' + b'\x00' * 30))

    def test_a_ping_to_someone_else_is_not_answered(self):
        responder = IcmpEchoResponder('100.64.0.1')
        self.assertIsNone(responder(self.echo_request('100.64.0.2', '8.8.8.8')))

    def test_ip_before_ipcp_opens_is_refused(self):
        server = make_server(auth=None, log=quiet)
        with self.assertRaises(PppError):
            server.send_ip(self.echo_request('100.64.0.2', '100.64.0.1'))

    def test_a_large_datagram_survives_framing_intact(self):
        server = make_server(auth=None, log=quiet)
        client = make_client(log=quiet)
        pump(server, client)
        # Every byte value, so escaping and ACCM are both exercised.
        payload = bytes(range(256)) * 4
        packet = self.echo_request('100.64.0.2', '100.64.0.1', payload)
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


class FakeTun:
    """A tun device that loops packets back, for testing without root.

    Creating a real one needs root and a system interface, so the tests drive
    the same ``TunBridge`` against this instead. What it cannot cover is the
    utun address-family header and the ifconfig/route calls; those are checked
    separately in TunFramingTests.
    """

    def __init__(self, mtu=1500, accept=True):
        self.mtu = mtu
        self.max_mtu = mtu
        self.name = 'faketun0'
        self.written = []
        self.queued = []
        self.accept = accept
        self.dropped_oversize = 0
        self.mtu_history = []

    def write_packet(self, packet):
        if len(packet) > self.mtu:
            self.dropped_oversize += 1
            return False
        if not self.accept:
            return False
        self.written.append(packet)
        return True

    def read_packets(self, limit=32):
        packets, self.queued = self.queued[:limit], self.queued[limit:]
        return packets

    def set_mtu(self, mtu):
        self.mtu = min(mtu, self.max_mtu)
        self.mtu_history.append(self.mtu)

    def summary(self):
        return f'{self.name} mtu={self.mtu}'


class TunBridgeTests(unittest.TestCase):
    """A client's IP goes to the network instead of terminating in the peer."""

    def setUp(self):
        try:
            from tun import TunBridge
        except ImportError as exc:          # pragma: no cover
            self.skipTest(f'tun unavailable: {exc}')
        self.device = FakeTun()
        self.bridge = TunBridge(self.device, log=quiet)
        self.server = make_server(auth=None, log=quiet)
        self.server.attach_network(self.bridge)
        self.client = make_client(log=quiet)
        pump(self.server, self.client)

    def test_a_client_datagram_reaches_the_device(self):
        packet = IpTests.echo_request('100.64.0.2', '1.1.1.1', b'outbound')
        self.client.send_ip(packet)
        pump(self.server, self.client, start=False)
        self.assertEqual(self.device.written, [packet])
        self.assertEqual(self.bridge.to_network, 1)

    def test_the_icmp_responder_stands_down_when_a_network_is_attached(self):
        """Otherwise it would answer pings the host was going to answer."""
        self.assertIsNone(self.server.handler)
        packet = IpTests.echo_request('100.64.0.2', '100.64.0.1')
        self.client.send_ip(packet)
        pump(self.server, self.client, start=False)
        # Forwarded to the kernel, not answered here.
        self.assertEqual(self.device.written, [packet])
        self.assertEqual(self.client.rx_ip, [])

    def test_a_packet_from_the_network_reaches_the_client(self):
        inbound = IpTests.echo_request('1.1.1.1', '100.64.0.2', b'inbound')
        self.device.queued.append(inbound)
        pump(self.server, self.client, start=False)
        self.assertEqual(self.client.rx_ip, [inbound])

    def test_nothing_is_sent_to_the_client_before_ipcp_opens(self):
        server = make_server(auth='chap', secrets={'ppp': 'ppp'}, log=quiet)
        device = FakeTun()
        from tun import TunBridge
        server.attach_network(TunBridge(device, log=quiet))
        server.start(0.0)
        device.queued.append(IpTests.echo_request('1.1.1.1', '100.64.0.2'))
        server.take()
        server.tick(1.0)
        # IPCP is not open, so the datagram must not go out as PPP.
        self.assertEqual(server.take(), b'')

    def test_the_bridge_does_not_queue_when_the_device_refuses(self):
        """The kernel's buffer is the queue; a second one here would add
        latency to the round trip this rig exists to measure."""
        self.device.accept = False
        self.client.send_ip(IpTests.echo_request('100.64.0.2', '1.1.1.1'))
        pump(self.server, self.client, start=False)
        self.assertEqual(self.device.written, [])
        self.assertEqual(self.bridge.refused, 1)
        self.assertEqual(self.bridge.to_network, 0)

    def test_the_interface_mtu_follows_the_negotiated_peer_mru(self):
        device = FakeTun()
        from tun import TunBridge
        server = make_server(auth=None, log=quiet)
        server.attach_network(TunBridge(device, log=quiet))
        client = make_client(log=quiet)
        client.lcp.want_mru = 576           # a client with a small MRU
        pump(server, client)
        self.assertEqual(server.lcp.peer_mru, 576)
        self.assertEqual(device.mtu, 576)

    def test_a_small_mru_client_cannot_ratchet_the_interface_down(self):
        self.device.set_mtu(576)
        self.device.set_mtu(9000)
        self.assertEqual(self.device.mtu, 1500)

    def test_an_oversized_inbound_datagram_is_dropped_not_truncated(self):
        # Larger than the peer said it can receive: sending it would make the
        # client drop a frame it cannot reassemble.
        self.server.lcp.peer_mru = 576
        self.device.queued.append(b'\x45' + b'\x00' * 999)
        pump(self.server, self.client, start=False)
        self.assertEqual(self.client.rx_ip, [])

    def test_rx_datagrams_are_not_accumulated_behind_a_network(self):
        """Holding every packet of a real session would grow without bound."""
        for _ in range(50):
            self.client.send_ip(IpTests.echo_request('100.64.0.2', '1.1.1.1'))
            pump(self.server, self.client, start=False)
        self.assertEqual(self.server.rx_ip, [])
        self.assertEqual(self.server.rx_ip_count, 50)


class TunFramingTests(unittest.TestCase):
    """The parts of the real device that do not need root to check."""

    def setUp(self):
        try:
            import tun
        except ImportError as exc:          # pragma: no cover
            self.skipTest(f'tun unavailable: {exc}')
        self.tun = tun

    def test_the_utun_header_is_the_address_family_in_network_order(self):
        # macOS prefixes every utun packet with AF_INET as a 4-byte big-endian
        # word. Getting this wrong makes the kernel silently ignore writes.
        self.assertEqual(self.tun.AF_INET_HEADER, b'\x00\x00\x00\x02')

    def test_opening_without_root_says_so_rather_than_failing_in_an_ioctl(self):
        import os
        if os.geteuid() == 0:
            self.skipTest('running as root')
        device = self.tun.TunDevice()
        with self.assertRaises(self.tun.TunError) as caught:
            device.open()
        self.assertIn('root', str(caught.exception))

    def test_configure_before_open_is_refused(self):
        with self.assertRaises(self.tun.TunError):
            self.tun.TunDevice().configure('100.64.0.1', '100.64.0.2')

    def test_linux_nat_rules_name_the_pool_and_the_uplink(self):
        commands = self.tun.nat_commands('ppp0', '100.64.0.0/10', 'eth0',
                                         system='Linux')
        flat = [' '.join(command) for command in commands]
        self.assertTrue(any('ip_forward=1' in line for line in flat))
        self.assertTrue(any('MASQUERADE' in line and '100.64.0.0/10' in line
                            and 'eth0' in line for line in flat))

    def test_the_pf_rule_names_the_pool_and_the_uplink(self):
        rule = self.tun.pf_nat_rule('100.64.0.0/10', 'en0')
        self.assertIn('nat on en0', rule)
        self.assertIn('from 100.64.0.0/10', rule)
        self.assertIn('-> (en0)', rule)


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
        self.assertEqual(self.client.peer.ipcp.local_address, '100.64.0.2')
        # Nothing may be lost in a link that is error-corrected by definition.
        self.assertEqual(self.server.peer.framer.fcs_errors, 0)
        self.assertEqual(self.client.peer.framer.fcs_errors, 0)

    def test_a_ping_crosses_the_v42_link_and_comes_back(self):
        self.run_link()
        self.assertTrue(self.client.peer.up)
        packet = IpTests.echo_request(
            self.client.peer.ipcp.local_address, '100.64.0.1', b'over-v42')
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
                peer.ipcp.local_address, '100.64.0.1', bytes([index]) * 400))
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


class EndpointGatingTests(unittest.TestCase):
    """The V.42 link must be serviced whenever anything consumes it.

    A regression test for a live call that produced no PPP at all: the media
    tick serviced the link only when a PTY was attached, and --ppp excludes
    --v42-pty because they claim the same link. The peer sent LCP, LAPM
    acknowledged it, and 512 bytes sat in rx_data for the whole call while
    nothing was ever sent back.

    The endpoint cannot be constructed without the emulator, so the property
    is exercised directly -- which is all the bug was.
    """

    def setUp(self):
        try:
            import eicon_adsp_sip
        except ImportError as exc:          # pragma: no cover
            self.skipTest(f'eicon_adsp_sip unavailable: {exc}')
        self.property = eicon_adsp_sip.EiconSipEndpoint.services_link.fget

    def check(self, pty, ppp_config):
        return self.property(SimpleNamespace(pty=pty, ppp_config=ppp_config))

    def test_ppp_alone_is_serviced(self):
        self.assertTrue(self.check(pty=None, ppp_config=PppConfig()))

    def test_a_terminal_alone_is_serviced(self):
        self.assertTrue(self.check(pty=object(), ppp_config=None))

    def test_neither_is_not_serviced(self):
        self.assertFalse(self.check(pty=None, ppp_config=None))


class V42UserNetTests(unittest.TestCase):
    """The endpoint's whole stack bar the data pump: PPP over a real V.42
    link, with the userspace NAT behind it, fetching over TCP.

    This is what `--ppp` assembles on a call, minus the bits on the line, and
    it is the only place the three pieces are exercised together.
    """

    def setUp(self):
        try:
            from v42_lapm import LapmEndpoint
        except ImportError as exc:          # pragma: no cover
            self.skipTest(f'v42_lapm unavailable: {exc}')
        from ppp import LapmPppLink
        from usernet import UserNetwork
        self.a = LapmEndpoint(log=quiet, detect=False, role='originator')
        self.b = LapmEndpoint(log=quiet, detect=False, role='answerer')
        self.net = UserNetwork(log=quiet)
        self.addCleanup(self.net.close)
        server = make_server(auth='chap', secrets={'ppp': 'ppp'}, log=quiet)
        server.attach_network(self.net)
        self.server = LapmPppLink(server, log=quiet)
        self.client = LapmPppLink(make_client(log=quiet), log=quiet)
        self.now = 0.0

    def run_link(self, ticks=400, bits=512):
        for _ in range(ticks):
            self.now += 0.02
            self.b.feed(self.a.take(bits))
            self.a.feed(self.b.take(bits))
            self.server.pump(self.a, self.now)
            self.client.pump(self.b, self.now)

    def test_a_tcp_fetch_crosses_v42_ppp_and_the_nat(self):
        import socket
        import struct
        import threading

        from usernet import (ACK, FIN, PROTO_TCP, PSH, SYN, build_ipv4,
                             build_tcp, parse_ipv4, parse_tcp)

        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        body = b'fetched over an emulated V.42 link'

        def serve():
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(body)
                connection.shutdown(socket.SHUT_WR)

        threading.Thread(target=serve, daemon=True).start()

        self.run_link()
        peer = self.client.peer
        self.assertTrue(peer.up, 'PPP did not come up over V.42')
        client_ip = peer.ipcp.local_address
        source = bytes(int(part) for part in client_ip.split('.'))
        target = bytes((127, 0, 0, 1))
        seq, ack = [7000], [0]

        def send(flags, payload=b'', options=b''):
            segment = build_tcp(source, target, 41000, port, seq[0], ack[0],
                                flags, 65535, payload, options)
            peer.send_ip(build_ipv4(source, target, PROTO_TCP, segment))
            seq[0] += len(payload) + (1 if flags & (SYN | FIN) else 0)

        def segments():
            found = []
            for packet in peer.rx_ip:
                parsed = parse_ipv4(packet)
                if parsed and parsed[2] == PROTO_TCP:
                    found.append(parse_tcp(parsed[3]))
            peer.rx_ip.clear()
            return found

        send(SYN, options=struct.pack('>BBH', 2, 4, 1460))
        for _ in range(40):
            self.run_link(ticks=10)
            for tcp in segments():
                if tcp['flags'] & SYN:
                    ack[0] = (tcp['seq'] + 1) & 0xFFFFFFFF
            if ack[0]:
                break
        self.assertTrue(ack[0], 'no SYN-ACK came back over the link')
        send(ACK)
        send(PSH | ACK, b'GET / HTTP/1.0\r\n\r\n')

        received = bytearray()
        for _ in range(80):
            self.run_link(ticks=10)
            for tcp in segments():
                if tcp['payload'] and tcp['seq'] == ack[0]:
                    received += tcp['payload']
                    ack[0] = (ack[0] + len(tcp['payload'])) & 0xFFFFFFFF
                    send(ACK)
            if bytes(received) == body:
                break
        self.assertEqual(bytes(received), body)


if __name__ == '__main__':
    unittest.main()
