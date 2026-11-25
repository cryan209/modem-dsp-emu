"""Native bearer fidelity, wire fixtures, link recovery and real PPP traffic."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_idi import IsdnOptions, isdn_cai, isdn_nl_assign_payload, parse_idi_parameters, IDI_DLC
from isdn_bchannel import NativeBChannel, V110Adapter, IsdnLink
from eicon_isdn_loopback import test_payload as run_payload, test_ppp as run_ppp, exchange
from v42_lapm import HdlcDecoder, octets_to_bits, FLAG_BITS
from ppp import PppPeer, PppConfig, OPT_ACCM


class NativeIsdnTest(unittest.TestCase):
    def test_all_profiles_transfer_every_octet_in_both_directions(self):
        for profile in (1, 2, 3, 6, 7, 8, 9, 10, 15):
            with self.subTest(profile=profile):
                result = run_payload(profile, size=4096, seconds=5)
                self.assertTrue(result['answer_to_caller_matches'])
                self.assertTrue(result['caller_to_answer_matches'])
                self.assertEqual(result['bad_fcs'], [0, 0])
                self.assertEqual(result['wire_octets_each_direction'], round(result['seconds'] * 8000))

    def test_all_profiles_authenticate_ppp_and_answer_icmp(self):
        for profile in (1, 2, 3, 6, 7, 8, 9, 10, 15):
            with self.subTest(profile=profile):
                result = run_ppp(profile, count=5, seconds=2)
                self.assertTrue(result['client_ipcp_up'])
                self.assertTrue(result['server_ipcp_up'])
                self.assertTrue(result['authenticated'])
                self.assertEqual(result['ping_reply_sequences'], list(range(1, 6)))
                self.assertEqual(result['ppp_fcs_errors'], [0, 0])
                self.assertEqual(result['link_failures'], [None, None])

    def test_v110_at_each_supported_user_rate(self):
        for rate in (9600, 19200, 38400):
            for profile in (2, 3):
                with self.subTest(rate=rate, profile=profile):
                    result = run_payload(profile, user_rate=rate, size=1024, seconds=3)
                    self.assertTrue(result['caller_to_answer_matches'])
                    self.assertTrue(result['answer_to_caller_matches'])

    def test_v110_zero_data_fixture_from_table_6e(self):
        adapter = V110Adapter()
        adapter.queue(b'\0' * 6)
        # Rows: 00, eight data rows 80, E row BF. Wire stores bit one
        # (oldest) in bit zero, reversing the Recommendation's row octet.
        self.assertEqual(adapter.frame(), bytes.fromhex('00 01 01 01 01 fd 01 01 01 01'))

    def test_v110_ra2_16k_fixture_and_frame_length(self):
        adapter = V110Adapter(9600)
        adapter.queue(b'\0' * 6)
        wire = adapter.frame()
        self.assertEqual(len(wire), 40)
        self.assertEqual(wire[:8], bytes.fromhex('fc fc fc fc fd fc fc fc'))
        self.assertEqual(wire[20:24], bytes.fromhex('fd ff ff ff'))

    def test_v110_recovers_frame_alignment_after_a_bad_framing_bit(self):
        tx, rx = V110Adapter(asynchronous=True), V110Adapter(asynchronous=True)
        damaged = bytearray(tx.frame())
        damaged[0] ^= 1
        rx.feed(damaged)
        tx.queue(b'hello')
        result = rx.feed(tx.frame() + tx.frame() + tx.frame())
        self.assertIn(b'hello', result)
        self.assertGreater(rx.framing_errors, 0)

    def test_56k_uses_seven_bits_per_8khz_bearer_slot(self):
        channel = NativeBChannel(IsdnOptions(10))
        channel.queue_data(b'\xff\x03\xc0\x21\x7e\x7d')
        wire = channel.take_wire(100)
        self.assertTrue(all(octet & 0x80 for octet in wire))
        bits = [(octet >> bit) & 1 for octet in wire for bit in range(7)]
        self.assertEqual(HdlcDecoder().feed(bits), [b'\xff\x03\xc0\x21\x7e\x7d'])

    def test_short_control_frame_after_partial_idle_flag_survives(self):
        channel = NativeBChannel(IsdnOptions(9))
        channel.idle_phase = 7
        channel.emit(b'\x01\x3f')
        # The last bit of the old idle flag must not consume the first bit
        # of the opening flag. Include its already-sent seven-bit prefix.
        bits = list(FLAG_BITS[:7]) + octets_to_bits(channel.take_wire(20))
        self.assertEqual(HdlcDecoder().feed(bits), [b'\x01\x3f'])

    def test_v110_sync_frame_survives_every_partial_idle_flag(self):
        for phase in range(8):
            with self.subTest(phase=phase):
                channel = NativeBChannel(IsdnOptions(2))
                channel.v110.idle_phase = phase
                channel.queue_data(b'\x01\x3f')
                bits = list(FLAG_BITS[:phase]) + list(channel.v110.tx)
                self.assertEqual(HdlcDecoder().feed(bits), [b'\x01\x3f'])

    def test_v120_default_lli_and_header_are_not_lapm(self):
        emitted = []
        link = IsdnLink(6, True, emitted.append, lambda _: None)
        link.start()
        self.assertEqual(emitted[0], b'\x08\x01\x7f')  # V.120 Table 4: C/R=0 command
        link.feed(b'\x0a\x01\x73')
        link.queue(b'abc')
        self.assertEqual(emitted[-1], b'\x08\x01\x00\x00\x83abc')
        answer = IsdnLink(6, False, emitted.append, lambda _: None)
        self.assertEqual(answer.address(True), b'\x08\x01')
        self.assertEqual(answer.address(False), b'\x0a\x01')

    def test_vendor_identification_xid_does_not_negotiate_compression(self):
        emitted = []
        link = IsdnLink(15, False, emitted.append, lambda _: None)
        vendor = bytes.fromhex('f00016000a4341504932302f58373501010302020400030120')
        link.feed(b'\x01\xbf' + vendor)
        self.assertEqual(emitted, [])
        self.assertFalse(link.compression)
        link.feed(b'\x01\x3f')
        self.assertTrue(link.connected)

    def test_x75_standard_sequence_control_and_address(self):
        emitted = []
        link = IsdnLink(1, True, emitted.append, lambda _: None)
        link.start()
        self.assertEqual(emitted, [b'\x01\x3f'])  # DTE SABM(P), modulo 8
        link.feed(b'\x01\x73')
        link.queue(b'abc')
        self.assertEqual(emitted[-1], b'\x01\x00abc')

    def test_acknowledged_links_recover_corrupted_payload_without_duplicates(self):
        for profile in (1, 6, 7, 15):
            with self.subTest(profile=profile):
                a = NativeBChannel(IsdnOptions(profile))
                b = NativeBChannel(IsdnOptions(profile), calling=False)
                for tick in range(20):
                    exchange((a, b), tick / 50)
                self.assertTrue(a.connected and b.connected)
                payload = bytes(range(256)) * 4
                a.queue_data(payload)
                wire = bytearray(a.take_wire())
                wire[10] ^= 1
                b.feed_wire(wire)
                result = bytearray(b.take_data())
                for tick in range(20, 300):
                    exchange((a, b), tick / 50)
                    result.extend(b.take_data())
                self.assertEqual(bytes(result), payload)
                self.assertGreater(b.hdlc.bad_fcs, 0)
                self.assertGreater(a.link.retransmits, 0)
                self.assertIsNone(a.link.failed)

    def test_lost_ua_does_not_lose_payload_already_sent_by_answerer(self):
        for profile in (1, 6, 7, 15):
            with self.subTest(profile=profile):
                a = NativeBChannel(IsdnOptions(profile))
                b = NativeBChannel(IsdnOptions(profile), calling=False)
                payload = bytes(range(256)) * 4
                b.queue_data(payload)
                result = bytearray()
                dropped = False
                for tick in range(300):
                    a.tick(tick / 50)
                    b.tick(tick / 50)
                    aw, bw = a.take_wire(), b.take_wire()
                    was_connected = b.connected
                    b.feed_wire(aw)
                    # Lose the answerer's first UA + initial information.
                    if was_connected and not dropped:
                        dropped = True
                    else:
                        a.feed_wire(bw)
                    result.extend(a.take_data())
                self.assertTrue(a.connected and b.connected)
                self.assertEqual(bytes(result), payload)
                self.assertIsNone(a.link.failed)
                self.assertIsNone(b.link.failed)

    def test_missing_establishment_response_retries_then_fails(self):
        channel = NativeBChannel(IsdnOptions(1))
        for second in range(1, 8):
            channel.take_wire(8000)
            channel.tick(second)
        self.assertFalse(channel.connected)
        self.assertEqual(channel.link.failed, 'ISDN link retry limit')
        with self.assertRaises(ConnectionError):
            channel.queue_data(b'late')

    def test_bad_ack_does_not_discard_unacknowledged_payload(self):
        link = IsdnLink(1, True, lambda _: None, lambda _: None)
        link.state = 'up'
        link.queue(b'abc')
        self.assertFalse(link.ack(4))
        self.assertEqual(list(link.unacked), [(0, b'abc')])

    def test_v42bis_negotiates_and_falls_back_without_compression(self):
        link = IsdnLink(15, True, lambda _: None, lambda _: None)
        link.start()
        self.assertEqual(link.state, 'xid')
        link.tick(1)
        self.assertEqual(link.state, 'establish')
        self.assertFalse(link.compression)
        result = run_payload(15, size=4096, seconds=5)
        self.assertEqual(result['compressed'], [True, True])
        self.assertTrue(result['caller_to_answer_matches'])

    def test_native_dlcs_do_not_disable_v42_and_mnp_as_a_modem(self):
        params = dict(parse_idi_parameters(isdn_nl_assign_payload(IsdnOptions(6))))
        self.assertEqual(params[IDI_DLC], bytes.fromhex('00 08 08 01 7f 07'))
        params = dict(parse_idi_parameters(isdn_nl_assign_payload(IsdnOptions(1))))
        self.assertEqual(params[IDI_DLC], bytes.fromhex('00 08'))
        self.assertEqual(isdn_cai(IsdnOptions(7)), b'\x08')

    def test_one_octet_hdlc_payload_survives_arbitrary_wire_chunks(self):
        a, b = NativeBChannel(IsdnOptions(9)), NativeBChannel(IsdnOptions(9))
        a.queue_data(b'\x7e')
        wire = a.take_wire(100)
        for octet in wire:
            b.feed_wire(bytes((octet,)))
        self.assertEqual(b.take_frames(), [b'\x7e'])

    def test_rnr_pauses_data_until_rr_acknowledgement(self):
        emitted = []
        link = IsdnLink(1, True, emitted.append, lambda _: None)
        link.state = 'up'
        link.queue(b'abc')
        link.feed(b'\x01\x05')
        link.queue(b'def')
        self.assertEqual(len(emitted), 1)
        link.feed(b'\x01\x21')
        self.assertEqual(emitted[-1], b'\x01\x02def')
        self.assertFalse(link.peer_busy)

    def test_missing_flag_cannot_grow_receive_buffer_without_bound(self):
        channel = NativeBChannel(IsdnOptions(9, max_frame=64))
        channel.feed_wire(b'\x7e')
        for _ in range(20):
            channel.feed_wire(b'\0' * 160)
        self.assertGreater(channel.oversize_frames, 0)
        self.assertLess(len(channel.hdlc.raw), 1000)

    def test_closed_bearer_rejects_data_and_does_not_deliver_late_octets(self):
        channel = NativeBChannel(IsdnOptions(8))
        channel.close()
        with self.assertRaises(ConnectionError):
            channel.queue_data(b'late')
        channel.feed_wire(b'late')
        self.assertEqual(channel.take_data(), b'')
        self.assertFalse(channel.connected)

    def test_synchronous_ppp_does_not_negotiate_async_accm(self):
        peer = PppPeer(PppConfig(synchronous=True), log=lambda _: None)
        self.assertNotIn(OPT_ACCM, dict(peer.lcp.local_options()))
        self.assertEqual(peer.lcp.review_peer_option(OPT_ACCM, b'\0' * 4)[0], 'rej')


if __name__ == '__main__':
    unittest.main()
