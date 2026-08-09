import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v42_lapm import (ADP_C, ADP_E, ADP_V42_SUPPORTED, HDLC_OPTIONAL_FUNCTIONS,
                      HdlcDecoder, LapmEndpoint, ODP_EVEN, ODP_ODD,
                      XidParameters, encode_frame, encode_xid_parameters,
                      fcs16, octets_to_bits, parse_xid_parameters)


class HdlcTests(unittest.TestCase):
    def test_known_x25_fcs(self):
        self.assertEqual(fcs16(b'123456789'), 0x906E)

    def test_fragmented_stuffed_round_trip(self):
        body = bytes((0x03, 0xAF, 0xFF, 0xF8, 0x7E, 0x00))
        wire = encode_frame(body)
        decoder = HdlcDecoder()
        got = []
        for start in range(0, len(wire), 7):
            got.extend(decoder.feed(wire[start:start + 7]))
        self.assertEqual(got, [body])
        self.assertEqual(decoder.good, 1)
        self.assertEqual(decoder.bad_fcs, 0)

    def test_bad_fcs_is_rejected(self):
        wire = encode_frame(b'\x03\xaf\x82')
        wire[12] ^= 1
        decoder = HdlcDecoder()
        self.assertEqual(decoder.feed(wire), [])
        self.assertEqual(decoder.bad_fcs, 1)


class XidTests(unittest.TestCase):
    def test_v42_parameter_round_trip(self):
        params = XidParameters(n401_tx=256, n401_rx=128,
                               k_tx=7, k_rx=15, optional_functions=1 << 17)
        self.assertEqual(parse_xid_parameters(encode_xid_parameters(params)),
                         params)

    def test_the_non_negotiable_optional_function_bits_are_set(self):
        # Table 11a/V.42 Note 1: bit positions 2, 4, 8, 9, 12 and 16 "shall" be
        # set in both the XID command and the XID response. Sending zero says
        # the sender has agreed to neither modulo-128 numbering (bit 9) nor a
        # 16-bit FCS (bit 16).
        for position in (2, 4, 8, 9, 12, 16):
            self.assertTrue(HDLC_OPTIONAL_FUNCTIONS & (1 << (position - 1)),
                            f'bit {position} is not set')
        # None of the four optional procedures of clause 10 is offered.
        for position in (3, 14, 17, 24):
            self.assertFalse(HDLC_OPTIONAL_FUNCTIONS & (1 << (position - 1)),
                             f'bit {position} offers an unimplemented option')

    def test_the_mask_is_encoded_low_order_octet_first(self):
        info = encode_xid_parameters(XidParameters())
        # FI, GI, GL, then PI=3 PL=4 and the 32-bit mask, bit 1 being the
        # low-order bit of the first octet transmitted.
        self.assertEqual(info[:5], b'\x82\x80\x00\x14\x03')
        self.assertEqual(info[5:10], b'\x04\x8a\x89\x00\x00')


class LapmTests(unittest.TestCase):
    def test_xid_and_sabme_response(self):
        logs = []
        endpoint = LapmEndpoint(log=logs.append, detect=False)
        # Drain constructor's idle flag, then send an XID command and SABME(P).
        endpoint.take(8)
        xid = b'\x03\xaf\x82\x80\x00\x00'
        endpoint.feed(encode_frame(xid) + encode_frame(b'\x03\x7f'))
        wire = endpoint.take(2048)
        decoder = HdlcDecoder()
        frames = decoder.feed(wire)
        self.assertIn(b'\x03\xaf' + encode_xid_parameters(XidParameters()),
                      frames)
        self.assertIn(b'\x03\x73', frames)
        self.assertTrue(endpoint.connected)
        self.assertEqual(endpoint.stats.xid_rx, 1)
        self.assertEqual(endpoint.stats.sabme_rx, 1)

    def test_the_xid_response_carries_the_conformance_mask(self):
        # The negotiated parameters are rebuilt from the peer's proposal; the
        # non-negotiable optional-function bits must survive that rebuild.
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        proposal = encode_xid_parameters(
            XidParameters(n401_tx=64, n401_rx=64, k_tx=7, k_rx=7,
                          optional_functions=0))
        endpoint.feed(encode_frame(b'\x03\xaf' + proposal))
        frames = HdlcDecoder().feed(endpoint.take(2048))
        response = next(f for f in frames if f[1] & 0xEF == 0xAF)
        params = parse_xid_parameters(response[2:])
        self.assertEqual(params.optional_functions, HDLC_OPTIONAL_FUNCTIONS)
        self.assertEqual((params.n401_tx, params.k_tx), (64, 7))

    def test_i_frame_is_acknowledged(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        endpoint.connected = True
        # N(S)=0, N(R)=0, payload "hi".
        endpoint.feed(encode_frame(b'\x03\x00\x00hi'))
        decoder = HdlcDecoder()
        frames = decoder.feed(endpoint.take(128))
        self.assertIn(b'\x03\x01\x02', frames)
        self.assertEqual(endpoint.rx_data, b'hi')


class CapturedCxXidTests(unittest.TestCase):
    """The real CX93001 XID, decoded from a live call's RXD trace.

    Its optional-functions value is 8a 89 00 -- the same six Table 11a bits
    this endpoint sends -- but carried in three octets where Table 11a Note 1
    says four. A responder answers in the form the initiator used, so against
    this peer the response comes out byte-identical to its own command.
    """

    CX_XID = bytes.fromhex(
        '03af8280001303038a8900050204000602040007010f08010f')
    # Current CX93001 call with S48=0/S46=136. GI=ff starts an unlengthened
    # user-data subfield; its 0x40 TLV names V44 and PI 0x41 value zero declines
    # compression in both directions. The V.42 group before it is complete.
    CX_V44_XID = bytes.fromhex(
        '03af8280001303038a8900050204000602040007010f08010f'
        'ff40035634344101004201034302020044020200450120460120'
        '4702040048020400')

    def test_the_captured_command_parses(self):
        params = parse_xid_parameters(self.CX_XID[2:])
        self.assertEqual(params.optional_functions, HDLC_OPTIONAL_FUNCTIONS)
        self.assertEqual(params.optional_functions_octets, 3)
        self.assertEqual((params.n401_tx, params.n401_rx), (128, 128))
        self.assertEqual((params.k_tx, params.k_rx), (15, 15))

    def test_the_response_mirrors_the_three_octet_encoding(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(self.CX_XID))
        frames = HdlcDecoder().feed(endpoint.take(2048))
        self.assertEqual(frames, [self.CX_XID])

    def test_a_four_octet_initiator_still_gets_four_octets_back(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(
            b'\x03\xaf' + encode_xid_parameters(XidParameters())))
        frames = HdlcDecoder().feed(endpoint.take(2048))
        params = parse_xid_parameters(frames[0][2:])
        self.assertEqual(params.optional_functions_octets, 4)

    def test_user_data_after_the_v42_group_does_not_invalidate_it(self):
        params = parse_xid_parameters(self.CX_V44_XID[2:])
        self.assertIsNotNone(params)
        self.assertEqual(params.optional_functions_octets, 3)
        self.assertEqual(params.optional_functions, HDLC_OPTIONAL_FUNCTIONS)
        self.assertEqual((params.n401_tx, params.n401_rx), (128, 128))

    def test_response_to_v44_announcement_uses_the_cx_v42_encoding(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(self.CX_V44_XID))
        frames = HdlcDecoder().feed(endpoint.take(2048))
        # Unsupported user data is not echoed, but the V.42 group is answered
        # byte-for-byte in the initiator's three-octet mask encoding.
        self.assertEqual(frames, [self.CX_V44_XID[:25]])


class FrameLengthTests(unittest.TestCase):
    def test_a_long_xid_is_not_rejected_after_n401_is_negotiated_down(self):
        # N401 bounds the information field of an I frame and nothing else.
        # The CX's XID is 77 octets; with N401 negotiated to 16 a blanket
        # length check answered its own retransmission with FRMR.
        endpoint = LapmEndpoint(log=lambda _: None, detect=False, n401=16)
        endpoint.take(8)
        long_xid = b'\x03\xaf' + encode_xid_parameters(XidParameters()) + b'\x00' * 64
        endpoint.feed(encode_frame(long_xid))
        self.assertEqual(endpoint.stats.frmr_tx, 0)
        self.assertEqual(endpoint.stats.xid_tx, 1)

    def test_an_oversized_i_frame_is_still_a_frame_rejection(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False, n401=4)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        endpoint.feed(encode_frame(b'\x03\x00\x00' + b'A' * 8))
        self.assertEqual(endpoint.stats.frmr_tx, 1)
        self.assertFalse(endpoint.connected)


class AddressingTests(unittest.TestCase):
    """Table 6/V.42: C/R depends on the direction and on who originated.

    Echoing the received address makes every command this endpoint originates
    -- I frames, the RR(P) window probe, DISC -- carry the C/R value that marks
    it as a response.
    """

    def test_answerer_commands_and_responses(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        self.assertEqual(endpoint.command_address, 0x01)
        self.assertEqual(endpoint.response_address, 0x03)
        self.assertEqual(endpoint.address, endpoint.response_address)

    def test_originator_commands_and_responses(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False,
                                role='originator')
        self.assertEqual(endpoint.command_address, 0x03)
        self.assertEqual(endpoint.response_address, 0x01)

    def test_a_received_frame_is_classified_by_the_cr_bit(self):
        answerer = LapmEndpoint(log=lambda _: None, detect=False)
        self.assertTrue(answerer._is_command(0x03))
        self.assertFalse(answerer._is_command(0x01))
        originator = LapmEndpoint(log=lambda _: None, detect=False,
                                  role='originator')
        self.assertFalse(originator._is_command(0x03))
        self.assertTrue(originator._is_command(0x01))

    def test_answerer_replies_to_a_command_and_commands_on_its_own_address(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False, n401=4)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))          # SABME(P) command
        endpoint.send(b'AAAA')
        frames = HdlcDecoder().feed(endpoint.take(2048))
        ua = [f for f in frames if f[1] & 0xEF == 0x63]
        i_frames = [f for f in frames if not f[1] & 0x01]
        self.assertEqual([f[0] for f in ua], [0x03])      # response
        self.assertEqual([f[0] for f in i_frames], [0x01])  # command

    def test_the_dlci_is_learned_from_the_peer(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        # DLCI 32 is in the "not reserved" range of Table 10; address is
        # DLCI<<2 | C/R<<1 | EA, so a command from the originator is 0x83.
        endpoint.feed(encode_frame(b'\x83\x7f'))
        self.assertEqual(endpoint.dlci, 32)
        self.assertEqual(endpoint.response_address, 0x83)
        self.assertEqual(endpoint.command_address, 0x81)
        frames = HdlcDecoder().feed(endpoint.take(512))
        self.assertEqual([f[0] for f in frames], [0x83])

    def test_the_f_bit_of_a_response_is_not_answered(self):
        # 8.4.7: only a polled *command* requires a final response. Answering
        # an RR response with F=1 is how two endpoints trade RRs forever.
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        HdlcDecoder().feed(endpoint.take(512))
        before = endpoint.stats.rr_tx
        endpoint.feed(encode_frame(b'\x01\x01\x01'))    # RR response, F=1
        self.assertEqual(endpoint.stats.rr_tx, before)
        endpoint.feed(encode_frame(b'\x03\x01\x01'))    # RR command, P=1
        self.assertEqual(endpoint.stats.rr_tx, before + 1)


class LapmTransmitTests(unittest.TestCase):
    """The outbound I-frame path: window, acknowledgement and recovery."""

    def setUp(self):
        self.endpoint = LapmEndpoint(log=lambda _: None, window=3, n401=4, detect=False)
        self.endpoint.take(8)
        # SABME establishes the link and zeroes both sequence directions.
        self.endpoint.feed(encode_frame(b'\x03\x7f'))
        self.decoder = HdlcDecoder()

    def frames(self, bits=4096):
        return self.decoder.feed(self.endpoint.take(bits))

    @staticmethod
    def i_frames(frames):
        return [f for f in frames if len(f) >= 3 and not f[1] & 0x01]

    def test_send_segments_into_n401_sized_i_frames(self):
        self.endpoint.send(b'ABCDEFGH')
        sent = self.i_frames(self.frames())
        # n401=4, so two full frames; window=3 allows both.
        self.assertEqual([f[3:] for f in sent], [b'ABCD', b'EFGH'])
        self.assertEqual([(f[1] >> 1) & 0x7F for f in sent], [0, 1])
        self.assertEqual(self.endpoint.stats.i_tx, 2)

    def test_window_limits_outstanding_frames(self):
        self.endpoint.send(b'A' * 40)  # 10 frames of 4 octets
        sent = self.i_frames(self.frames())
        self.assertEqual(len(sent), 3)          # window=3
        self.assertEqual(self.endpoint.outstanding, 3)
        # RR acknowledging N(R)=2 releases two and admits two more.
        self.endpoint.feed(encode_frame(b'\x03\x01\x04'))
        self.assertEqual(self.endpoint.va, 2)
        self.assertEqual(self.endpoint.outstanding, 1)
        sent = self.i_frames(self.frames())
        self.assertEqual([(f[1] >> 1) & 0x7F for f in sent], [3, 4])

    def test_i_frame_carries_current_receive_state(self):
        # Receive one I frame, then send: our N(R) must acknowledge it.
        self.endpoint.feed(encode_frame(b'\x03\x00\x00hi'))
        self.endpoint.send(b'ok')
        sent = self.i_frames(self.frames())
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0][2] >> 1) & 0x7F, 1)  # N(R) = V(R) = 1

    def test_rej_retransmits_from_nr(self):
        self.endpoint.send(b'AAAABBBBCCCC')
        self.assertEqual(len(self.i_frames(self.frames())), 3)
        self.endpoint.stats.i_retx = 0
        # REJ with N(R)=1: frame 0 is acknowledged, resend from 1.
        self.endpoint.feed(encode_frame(b'\x03\x09\x02'))
        resent = self.i_frames(self.frames())
        self.assertEqual([f[3:] for f in resent], [b'BBBB', b'CCCC'])
        self.assertEqual(self.endpoint.stats.i_retx, 2)
        self.assertEqual(self.endpoint.stats.rej_rx, 1)

    def test_rnr_stops_the_window_and_rr_resumes_it(self):
        self.endpoint.feed(encode_frame(b'\x03\x05\x00'))  # RNR N(R)=0
        self.endpoint.send(b'AAAA')
        self.assertEqual(self.i_frames(self.frames()), [])
        self.assertTrue(self.endpoint.peer_busy)
        self.endpoint.feed(encode_frame(b'\x03\x01\x00'))  # RR N(R)=0
        self.assertFalse(self.endpoint.peer_busy)
        self.assertEqual([f[3:] for f in self.i_frames(self.frames())], [b'AAAA'])

    def test_stalled_window_polls_then_retransmits(self):
        endpoint = LapmEndpoint(log=lambda _: None, window=2, n401=4,
                                poll_after=3, retransmit_after=5, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        endpoint.send(b'AAAA')
        decoder = HdlcDecoder()
        decoder.feed(endpoint.take(2048))       # first transmission
        self.assertEqual(endpoint.stats.poll_tx, 0)
        for _ in range(3):
            decoder.feed(endpoint.take(2048))
        self.assertEqual(endpoint.stats.poll_tx, 1)   # probed, not resent
        self.assertEqual(endpoint.stats.i_retx, 0)
        for _ in range(3):
            decoder.feed(endpoint.take(2048))
        self.assertGreaterEqual(endpoint.stats.i_retx, 1)  # then went back N

    def test_sabme_resets_transmit_state(self):
        self.endpoint.send(b'AAAA')
        self.frames()
        self.assertEqual(self.endpoint.vs, 1)
        self.endpoint.feed(encode_frame(b'\x03\x7f'))
        self.assertEqual((self.endpoint.vs, self.endpoint.va), (0, 0))
        self.assertEqual(self.endpoint.unacked, {})

    def test_sequence_numbers_wrap_at_128(self):
        endpoint = LapmEndpoint(log=lambda _: None, window=1, n401=1, detect=False)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        endpoint.send(b'x' * 130)
        for expected in range(130):
            endpoint.take(512)
            self.assertEqual(endpoint.vs, (expected + 1) & 0x7F)
            # Acknowledge each frame so the window of 1 keeps moving.
            nr = (expected + 1) & 0x7F
            endpoint.feed(encode_frame(bytes((0x03, 0x01, (nr << 1) & 0xFE))))
        self.assertEqual(endpoint.tx_stream, b'')


class DetectionPhaseTests(unittest.TestCase):
    """V.42 7.2.1 answerer role: mark, then ADP once the ODP is seen."""

    MARK = [1] * 12

    def odp(self, count):
        """`count` DC1s of alternating parity, separated by mark fill."""
        bits = []
        for index in range(count):
            bits += list(ODP_EVEN if index % 2 == 0 else ODP_ODD) + self.MARK
        return bits

    def test_answerer_starts_on_mark_not_flags(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        self.assertEqual(endpoint.detection, 'mark')
        self.assertEqual(endpoint.take(64), [1] * 64)

    def test_four_alternating_dc1s_trigger_the_adp(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(self.odp(3))
        self.assertEqual(endpoint.detection, 'mark')   # three is not enough
        endpoint.feed(self.odp(1) if endpoint._odp_parity else self.odp(2)[10:])
        self.assertEqual(endpoint.detection, 'adp')
        self.assertEqual(endpoint.stats.adp_tx, 1)

    def test_adp_is_v42_supported_and_repeated_ten_times(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(self.odp(4))
        sent = endpoint.take(len(ADP_V42_SUPPORTED) * 10)
        self.assertEqual(sent, list(ADP_V42_SUPPORTED) * 10)
        # (E) and (C) are both present, which is what distinguishes "V.42
        # supported" from the (E)+(Null) "no error-correcting protocol" pattern.
        self.assertIn(list(ADP_E), [sent[:len(ADP_E)]])
        self.assertIn(list(ADP_C), [sent[len(ADP_E) + 12:][:len(ADP_C)]])

    def test_flags_follow_the_adp(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(self.odp(4))
        endpoint.take(len(ADP_V42_SUPPORTED) * 10)
        self.assertEqual(endpoint.detection, 'adp')
        tail = endpoint.take(16)
        self.assertEqual(endpoint.detection, 'protocol')
        self.assertEqual(tail, list(FLAG := (0, 1, 1, 1, 1, 1, 1, 0)) * 2)

    def test_repeated_same_parity_dc1s_do_not_count(self):
        endpoint = LapmEndpoint(log=lambda _: None)
        for _ in range(8):
            endpoint.feed(list(ODP_EVEN) + self.MARK)
        self.assertEqual(endpoint.detection, 'mark')

    def test_lapm_frame_enters_protocol_without_an_odp(self):
        # An originator with detection disabled goes straight to SABME.
        endpoint = LapmEndpoint(log=lambda _: None)
        endpoint.feed(encode_frame(b'\x03\x7f'))
        self.assertEqual(endpoint.detection, 'protocol')
        self.assertTrue(endpoint.connected)

    def test_detection_timeout_falls_back_to_raw(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect_timeout=3)
        for _ in range(6):
            endpoint.take(8)
        self.assertEqual(endpoint.detection, 'raw')
        endpoint.send(b'X')
        self.assertEqual(endpoint.take(8), [0, 0, 0, 1, 1, 0, 1, 0])

    def test_detect_false_reproduces_the_old_behaviour(self):
        endpoint = LapmEndpoint(log=lambda _: None, detect=False)
        self.assertEqual(endpoint.detection, 'protocol')
        self.assertEqual(endpoint.take(8), [0, 1, 1, 1, 1, 1, 1, 0])

    def test_originator_odp_adp_and_lapm_establishment(self):
        logs = []
        originator = LapmEndpoint(log=logs.append, role='originator')
        answerer = LapmEndpoint(log=logs.append, role='answerer')
        for _ in range(30):
            answerer.feed(originator.take(512))
            originator.feed(answerer.take(512))
            if originator.connected and answerer.connected:
                break
        self.assertTrue(originator.stats.adp_rx >= 2)
        self.assertTrue(originator.connected)
        self.assertTrue(answerer.connected)
        self.assertGreaterEqual(answerer.stats.odp_rx, 4)


class RawFallbackRecoveryTests(unittest.TestCase):
    """The non-error-corrected fallback must not be a one-way door.

    A peer with V.42 detection disabled never sends an ODP, so T400 expires
    and its SABME arrives strictly afterwards. V.42 7.2.1.3 makes receipt of
    an LAPM frame the start of the protocol phase regardless.
    """

    def _fallen_back(self):
        lapm = LapmEndpoint(log=lambda *a: None, detect=True)
        lapm._enter_raw('T400 expired')
        self.assertTrue(lapm.raw_mode)
        return lapm

    def test_a_frame_after_fallback_enters_the_protocol_phase(self):
        lapm = self._fallen_back()
        sabme = bytes((lapm.address, lapm.SABME_MASKED | 0x10))
        lapm.feed(list(encode_frame(sabme)))
        self.assertEqual(lapm.detection, 'protocol')
        self.assertFalse(lapm.raw_mode)
        self.assertEqual(lapm.stats.sabme_rx, 1)

    def test_the_misread_raw_octets_are_discarded_on_recovery(self):
        lapm = self._fallen_back()
        lapm.feed([1, 0] * 64)                    # noise read as raw octets
        self.assertTrue(lapm.rx_data)
        lapm.feed(list(encode_frame(bytes((lapm.address,
                                           lapm.SABME_MASKED | 0x10)))))
        self.assertEqual(lapm.detection, 'protocol')
        self.assertEqual(bytes(lapm.rx_data), b'')

    def test_raw_data_still_flows_while_no_frame_arrives(self):
        lapm = self._fallen_back()
        lapm.feed(octets_to_bits(b'hi'))
        self.assertEqual(bytes(lapm.rx_data), b'hi')
        self.assertTrue(lapm.raw_mode)


class DetectionDiagnosticTests(unittest.TestCase):
    """T400 has two causes that the ODP counter cannot tell apart.

    Live calls trained to 46667/21600 and still fell back to non-error-
    corrected operation, and "no ODP seen" does not say whether the peer never
    sent one or sent one the path corrupted. The mark ratio does.
    """

    def endpoint(self):
        return LapmEndpoint(log=lambda _: None, detect=True, role='answerer')

    def test_an_idle_peer_is_reported_as_mark(self):
        endpoint = self.endpoint()
        endpoint.feed([1] * 4000)
        summary = endpoint.detection_summary()
        self.assertIn('100.0% ones', summary)
        self.assertIn('mark/idle', summary)

    def test_real_data_is_reported_as_data(self):
        import random
        random.seed(1)
        endpoint = self.endpoint()
        endpoint.feed([random.randint(0, 1) for _ in range(4000)])
        self.assertIn('peer is sending data', endpoint.detection_summary())

    def test_nothing_received_says_so(self):
        self.assertIn('no bits reached the detector',
                      self.endpoint().detection_summary())

    def test_the_summary_reaches_the_fallback_message(self):
        logged = []
        endpoint = LapmEndpoint(log=logged.append, detect=True,
                                role='answerer', detect_timeout=2)
        for _ in range(5):
            endpoint.feed([1] * 64)
            endpoint.take(64)
        self.assertTrue(endpoint.raw_mode)
        fallback = [line for line in logged if 'T400 expired' in line]
        self.assertTrue(fallback)
        self.assertIn('% ones', fallback[0])


class RetryLimitTests(unittest.TestCase):
    """Recovery has to terminate.

    A live V.90 call showed 40,363 retransmissions for 100 I frames sent,
    against 63 on a clean call in the same run -- the difference between "the
    connection is perfect" and "the connection is really glitchy". The cause
    was that `_retransmit_from` cleared the retry counter that `_service` had
    just tested, so N400 was unreachable from the timeout path and go-back-N
    repeated for ever, which on a lossy line is itself a source of loss.
    """

    def endpoint(self, **kwargs):
        options = dict(log=lambda _: None, window=3, n401=4, detect=False,
                       poll_after=2, retransmit_after=4, n400=3)
        options.update(kwargs)
        endpoint = LapmEndpoint(**options)
        endpoint.take(8)
        endpoint.feed(encode_frame(b'\x03\x7f'))    # SABME establishes
        return endpoint

    def run_until_idle(self, endpoint, services=500):
        for _ in range(services):
            endpoint.take(64)
            if not endpoint.connected:
                return
        self.fail('the endpoint never gave up on an unacknowledged window')

    def test_a_peer_that_never_acknowledges_makes_the_link_give_up(self):
        endpoint = self.endpoint()
        endpoint.send(b'ABCDEFGH')
        self.run_until_idle(endpoint)
        self.assertFalse(endpoint.connected)
        # Two frames, three recovery attempts: single figures, not thousands.
        self.assertLess(endpoint.stats.i_retx, 20)

    def test_a_window_that_moves_resets_the_budget(self):
        """Progress must not be punished: an acknowledged frame restores the
        full retry allowance for the next one."""
        endpoint = self.endpoint()
        endpoint.send(b'ABCD' * 4)
        for _ in range(6):
            endpoint.take(64)
        # Acknowledge one frame, the way a healthy peer would.
        endpoint.feed(encode_frame(b'\x03\x01\x02'))
        self.assertTrue(endpoint.connected)
        self.assertEqual(endpoint._retries, 0)

    def test_the_link_is_re_established_before_it_is_given_up_on(self):
        """A one-way outage costs a re-establishment, not the call."""
        logged = []
        endpoint = self.endpoint(log=logged.append)
        endpoint.send(b'ABCDEFGH')
        for _ in range(40):
            endpoint.take(64)
            if endpoint.stats.reestablish:
                break
        self.assertEqual(endpoint.stats.reestablish, 1)
        self.assertFalse(endpoint.connected)
        self.assertIsNone(endpoint.failed)
        self.assertTrue(any('re-establish' in line for line in logged))
        # The unacknowledged window went with the link it belonged to.
        self.assertEqual(endpoint.outstanding, 0)
        # UA(F) answers the SABME and the link is back, as a new generation.
        before = endpoint.generation
        endpoint.feed(encode_frame(b'\x03\x73'))
        self.assertTrue(endpoint.connected)
        self.assertEqual(endpoint.generation, before + 1)

    def test_a_failure_is_announced_once_and_stops_the_machinery(self):
        """The retry limit used to re-fire on every datagram after it was hit:
        one live call logged 222,902 identical disconnect lines."""
        logged = []
        endpoint = self.endpoint(log=logged.append)
        endpoint.send(b'ABCDEFGH')
        for _ in range(500):
            endpoint.take(64)
        self.assertEqual(endpoint.failed, 'T401 SABME retry limit')
        self.assertEqual(
            len([line for line in logged if 'disconnected' in line]), 1)
        self.assertEqual(endpoint.outstanding, 0)
        self.assertFalse(endpoint.data_ready)

    def test_a_failed_link_still_answers_a_peer_that_establishes_again(self):
        endpoint = self.endpoint()
        endpoint.send(b'ABCDEFGH')
        for _ in range(500):
            endpoint.take(64)
        self.assertIsNotNone(endpoint.failed)
        endpoint.feed(encode_frame(b'\x03\x7f'))    # SABME
        self.assertTrue(endpoint.connected)
        self.assertIsNone(endpoint.failed)
        # And the re-establishment budget came back with the link.
        self.assertEqual(endpoint._reestablish_left, endpoint.reestablish)

    def test_a_received_disc_clears_the_window(self):
        endpoint = self.endpoint()
        endpoint.send(b'ABCDEFGH')
        endpoint.take(64)
        self.assertTrue(endpoint.outstanding)
        endpoint.feed(encode_frame(b'\x03\x53'))    # DISC(P)
        self.assertFalse(endpoint.connected)
        self.assertEqual(endpoint.outstanding, 0)
        self.assertEqual(endpoint.stats.ua_tx, 2)   # SABME's, then DISC's

    def test_an_explicit_rej_is_not_counted_as_a_failed_attempt(self):
        """A REJ proves the peer is listening, which a timeout does not."""
        endpoint = self.endpoint()
        endpoint.send(b'ABCDEFGH')
        # Short of the retry limit, so the link is still up to observe.
        for _ in range(5):
            endpoint.take(64)
        endpoint._retries = 2
        endpoint.feed(encode_frame(b'\x03\x09\x00'))     # REJ, N(R)=0
        self.assertEqual(endpoint.stats.rej_rx, 1)
        self.assertEqual(endpoint._retries, 0)
        self.assertTrue(endpoint.connected)


if __name__ == '__main__':
    unittest.main()
