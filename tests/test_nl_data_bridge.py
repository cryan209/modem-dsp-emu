"""The NL N_DATA bearer bridge: entity addressing, gating and flow control.

NativeMipsModem cannot be constructed without a booted emulator, and none of
the bridge's state machine depends on one: it reads DM words, the assigned
entity id and the outstanding-request flags, and calls post_request().  The
tests therefore build a bare instance and drive those methods directly,
stubbing post_request so the PR ring is not involved.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

try:
    import eicon_mips_shim as shim_module
except ImportError as exc:            # unicorn is not installed
    shim_module = None
    _import_error = exc

import eicon_idi


DATASTATE = 0x3FC2
V90D = 0x026A


class _Shim:
    def __init__(self, nl_connected=True):
        self.nl_connected = nl_connected


def _card(*, entity_id=0x51, connected=True, resident=V90D,
          datastate=0x00C6, nl_data_mode=True, lapm=None,
          tx_v42=True, lapm_active=True, nl_data_forced=False):
    card = object.__new__(shim_module.NativeMipsModem)
    card.shim = _Shim(connected)
    card.tx_v42 = tx_v42
    card.tx_prbs = False
    card._lapm_active = lapm_active
    card._nl_rx_seen = False
    card._rx_trace = None
    card.idi_context = (0x1000, 0x2000, 0x3000)
    card.nl_entity_id = entity_id
    card.nl_data_queue = shim_module.collections.deque()
    card.nl_data_mode = nl_data_mode
    card.nl_data_forced = nl_data_forced
    card.lapm = lapm
    card.resident = resident
    card.dm = {DATASTATE: datastate}
    card._nl_busy = False
    card._nl_fc = False
    card._nl_reference = 0
    card._nl_posted = 0
    card._nl_accepted = 0
    card._nl_rejected = 0
    card._nl_tx_octets = 0
    card._nl_rx_octets = 0
    card._nl_gate_reported = False
    card._nl_tx_bits = []
    card.tx_payload_datagrams = 0
    card.tx_fill_datagrams = 0
    card._tx_datagram_bits = None
    return card


class _Posted:
    """Capture what post_request() was asked to send."""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        self._saved = shim_module.post_request
        shim_module.post_request = self._record
        return self

    def __exit__(self, *exc):
        shim_module.post_request = self._saved

    def _record(self, shim, sr, req, entity_id, channel, payload, reference=0):
        self.calls.append({'req': req, 'id': entity_id, 'ch': channel,
                           'payload': bytes(payload), 'ref': reference})
        return 0x0100 * len(self.calls)


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class NlDataAddressingTests(unittest.TestCase):
    def test_request_uses_the_assigned_entity_id(self):
        # isdn.c:3282 sends every post-ASSIGN request on C->Net.Id.  A
        # hardcoded Id addressed an entity that was never assigned, and the
        # firmware dropped the request without a return code.
        card = _card(entity_id=0x51)
        card.nl_data_queue.append(b'payload')
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(len(posted.calls), 1)
        self.assertEqual(posted.calls[0]['id'], 0x51)
        self.assertEqual(posted.calls[0]['req'], eicon_idi.N_DATA)
        self.assertEqual(posted.calls[0]['payload'], b'payload')

    def test_nothing_is_posted_without_an_assigned_entity(self):
        card = _card(entity_id=None)
        card.nl_data_queue.append(b'payload')
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(posted.calls, [])

    def test_oversized_payload_is_split_and_the_remainder_kept(self):
        card = _card()
        card.nl_data_queue.append(bytes(range(256)) * 2)
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(len(posted.calls[0]['payload']), 270)
        self.assertEqual(len(card.nl_data_queue), 1)
        self.assertEqual(len(card.nl_data_queue[0]), 512 - 270)


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class NlDataGateTests(unittest.TestCase):
    def test_no_data_before_n_connect_is_accepted(self):
        card = _card(connected=False)
        card.nl_data_queue.append(b'early')
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(posted.calls, [])
        self.assertEqual(len(card.nl_data_queue), 1)

    def test_no_data_before_the_data_pump_reports_synchronous(self):
        # The first CX call opened the gate at DATASTATE=0x0000, because the
        # DATASTATE test only covers the V.34/V.90 pages and 0x0258 was still
        # resident. _lapm_active is the pump's own statement, whatever page is.
        card = _card(resident=0x0258, lapm_active=False)
        card.nl_data_queue.append(b'early')
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(posted.calls, [])

    def test_no_data_before_the_synchronous_data_state(self):
        # The 6.4 s submission happened here: training, ten seconds before
        # CONNECT, with no bearer able to carry it.
        card = _card(datastate=0x0040)
        card.nl_data_queue.append(b'training')
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(posted.calls, [])

    def test_data_flows_once_the_pump_reaches_0xc6(self):
        card = _card(datastate=0x0040)
        card.nl_data_queue.append(b'held')
        with _Posted() as posted:
            card._service_n_data()
            self.assertEqual(posted.calls, [])
            card.dm[DATASTATE] = 0x00C8
            card._service_n_data()
        self.assertEqual(len(posted.calls), 1)
        self.assertEqual(posted.calls[0]['payload'], b'held')


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class NlFlowControlTests(unittest.TestCase):
    def test_only_one_request_is_outstanding_at_a_time(self):
        card = _card()
        card.nl_data_queue.extend([b'one', b'two', b'three'])
        with _Posted() as posted:
            for _ in range(10):
                card._service_n_data()
        self.assertEqual(len(posted.calls), 1)
        self.assertEqual(len(card.nl_data_queue), 2)

    def test_ok_releases_the_next_request(self):
        card = _card()
        card.nl_data_queue.extend([b'one', b'two'])
        with _Posted() as posted:
            card._service_n_data()
            card._nl_return_code(eicon_idi.RC_OK, card.nl_entity_id, 0, 1)
            card._service_n_data()
        self.assertEqual([call['payload'] for call in posted.calls],
                         [b'one', b'two'])
        self.assertEqual(card._nl_accepted, 1)

    def test_ok_fc_blocks_until_ready_int(self):
        card = _card()
        card.nl_data_queue.extend([b'one', b'two'])
        with _Posted() as posted:
            card._service_n_data()
            card._nl_return_code(eicon_idi.OK_FC, card.nl_entity_id, 0, 1)
            card._service_n_data()
            self.assertEqual(len(posted.calls), 1)
            card._nl_return_code(eicon_idi.READY_INT, card.nl_entity_id, 0, 0)
            card._service_n_data()
        self.assertEqual(len(posted.calls), 2)

    def test_a_return_code_for_another_entity_is_ignored(self):
        card = _card(entity_id=0x51)
        card.nl_data_queue.extend([b'one', b'two'])
        with _Posted() as posted:
            card._service_n_data()
            card._nl_return_code(eicon_idi.RC_OK, 0x12, 0, 1)
            card._service_n_data()
        self.assertEqual(len(posted.calls), 1)

    def test_rejection_is_counted_and_the_entity_is_released(self):
        card = _card()
        card.nl_data_queue.extend([b'one', b'two'])
        with _Posted() as posted:
            card._service_n_data()
            card._nl_return_code(eicon_idi.WRONG_ID, card.nl_entity_id, 0, 1)
            card._service_n_data()
        self.assertEqual(len(posted.calls), 2)
        self.assertEqual(card._nl_rejected, 1)
        self.assertEqual(card._nl_accepted, 0)

    def test_references_are_distinct_per_request(self):
        card = _card()
        card.nl_data_queue.extend([b'one', b'two'])
        with _Posted() as posted:
            card._service_n_data()
            card._nl_return_code(eicon_idi.RC_OK, card.nl_entity_id, 0, 1)
            card._service_n_data()
        self.assertNotEqual(posted.calls[0]['ref'], posted.calls[1]['ref'])
        self.assertTrue(all(call['ref'] for call in posted.calls))


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class NlElasticStoreTests(unittest.TestCase):
    def test_whole_octets_only_are_drained(self):
        card = _card()
        card._nl_tx_bits = [1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1]
        self.assertEqual(card._nl_take_tx(270), bytes((0x55,)))
        self.assertEqual(card._nl_tx_bits, [1, 1, 1])

    def test_low_order_bit_first_round_trips_through_lapm(self):
        from v42_lapm import octets_to_bits
        card = _card()
        # HDLC transmits the low-order bit of each octet first, which is how
        # LAPM lays its stream out; the elastic store must agree or every
        # frame reaches the peer bit-reversed.
        card._nl_tx_bits = list(octets_to_bits(b'\x03\xaf'))
        self.assertEqual(card._nl_take_tx(2), b'\x03\xaf')

    def test_an_empty_store_posts_nothing(self):
        # The bridge used to queue whatever the LAPM take returned without
        # checking it, so an empty take still left an entry to post.
        card = _card(lapm=None)
        with _Posted() as posted:
            card._service_n_data()
        self.assertEqual(posted.calls, [])


class _RecordingLapm:
    """Stands in for LapmEndpoint, recording only what reached the decoder."""

    def __init__(self):
        self.fed = []

    def feed(self, bits):
        self.fed.extend(bits)


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class NlReceivePathTests(unittest.TestCase):
    """One decoder, one bit source.

    In NL mode the receive stream arrives as N_DATA indications.  The DSP
    receive mailbox must still be acknowledged -- the DSP stalls otherwise --
    but its bits must not also reach the HdlcDecoder, or the two interleaved
    streams desynchronise the flag search and fail the FCS on everything.
    """

    def _rx_card(self, nl_data_mode, nl_rx_seen=False):
        card = _card(nl_data_mode=nl_data_mode, lapm=_RecordingLapm())
        card._lapm_active = True
        card._nl_rx_seen = nl_rx_seen
        # DATASTATESpeed index 16 is 24000 bit/s; bit 13 clear selects the
        # V.34 rate format, so the datagram is 24000/2400 = 10 bits.
        card.dm[0x3F62] = 16
        card.dm[0x3FAD] = 0x2000           # RXD0 valid
        card.dm[0x3FAE] = 0xABC0
        card.dm[0x3FAF] = 0x0000
        return card

    def test_nl_mode_still_decodes_the_mailbox_until_an_indication_arrives(self):
        # What the first live CX call established: the firmware accepted every
        # N_DATA request and returned no N_DATA indication, so the mailbox is
        # the only receive source there is.  Suppressing it on the assumption
        # that indications would replace it starved LAPM for a whole call.
        card = self._rx_card(nl_data_mode=True)
        card._service_rx_data()
        self.assertEqual(card.lapm.fed,
                         [(0xABC0 >> (15 - bit)) & 1 for bit in range(10)])

    def test_an_observed_indication_takes_the_mailbox_out_of_the_decoder(self):
        card = self._rx_card(nl_data_mode=True, nl_rx_seen=True)
        card._service_rx_data()
        self.assertEqual(card.lapm.fed, [])

    def test_the_mailbox_is_acknowledged_either_way(self):
        for seen in (False, True):
            card = self._rx_card(nl_data_mode=True, nl_rx_seen=seen)
            card._service_rx_data()
            self.assertEqual(card.dm[0x3FAD] & 0x2000, 0,
                             'the DSP stalls if the datagram is never consumed')

    def test_the_synchronous_path_always_decodes_the_mailbox(self):
        card = self._rx_card(nl_data_mode=False)
        card._service_rx_data()
        self.assertEqual(card.lapm.fed,
                         [(0xABC0 >> (15 - bit)) & 1 for bit in range(10)])
        self.assertEqual(card.dm[0x3FAD] & 0x2000, 0)


class _CountingLapm:
    """Stands in for LapmEndpoint on the transmit side."""

    def __init__(self, pattern=(0, 1)):
        self.pattern = pattern
        self.taken = 0

    def take(self, count):
        self.taken += count
        return [self.pattern[i % len(self.pattern)] for i in range(count)]


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class TxMailboxOwnershipTests(unittest.TestCase):
    """A host test source and TIKRNL must not answer the same TX request."""

    def _card_with_tikrnl_stores(self):
        card = object.__new__(shim_module.NativeMipsModem)
        card.pm = [0] * 0x4000
        base = 0x0123              # deliberately not an extracted/live address
        for offset, opcode in shim_module.TIKRNL_TXD_STORE_SIGNATURE:
            card.pm[base + offset] = opcode
        return card, base

    def test_claiming_the_mailbox_suppresses_every_tikrnl_txd_store(self):
        card, base = self._card_with_tikrnl_stores()
        card._claim_tx_mailbox()
        self.assertEqual(
            [card.pm[base + offset] for offset, _ in
             shim_module.TIKRNL_TXD_STORE_SIGNATURE],
            [0, 0, 0, 0, 0])

    def test_an_unknown_tikrnl_build_is_not_silently_patched(self):
        card, base = self._card_with_tikrnl_stores()
        card.pm[base + 0x68] ^= 1
        with self.assertRaisesRegex(RuntimeError, 'matched 0 times'):
            card._claim_tx_mailbox()
        # Validation is atomic: no store was changed before the bad signature
        # was rejected.
        self.assertEqual(card.pm[base], 0x93F05A)


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class TxPatternFramingTests(unittest.TestCase):
    """The raw peer consumes V.14 start-stop characters, not bare octets."""

    def test_pattern_text_is_encoded_as_8n1_low_order_bit_first(self):
        self.assertEqual(
            shim_module._start_stop_pattern_bits(b'A'),
            (0, 1, 0, 0, 0, 0, 0, 1, 0, 1))

    def test_each_pattern_character_has_its_own_start_and_stop_bit(self):
        bits = shim_module._start_stop_pattern_bits(b'AB')
        self.assertEqual(len(bits), 20)
        self.assertEqual((bits[0], bits[9], bits[10], bits[19]),
                         (0, 1, 0, 1))


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class NlTransmitPathTests(unittest.TestCase):
    """The transmit direction is gated on the same evidence as the receive one.

    Diverting to NL before an N_DATA indication has been seen puts mark fill in
    the transmit mailbox -- the one path known to reach the line -- and hands
    the LAPM stream to an entity never shown to carry it.  That is the 73-XID
    call: the CX was answered 73 times over NL, heard mark, and retransmitted
    XID for the whole call instead of sending SABME.
    """

    def _tx_card(self, **kwargs):
        card = _card(lapm=_CountingLapm(), **kwargs)
        card.dm[0x3F61] = 0x2028       # V.90 speed format, 29 bits/datagram
        card.dm[0x3F62] = 16
        card.dm[0x3FAD] = 0
        return card

    def test_nl_mode_transmits_on_the_mailbox_until_an_indication_arrives(self):
        card = self._tx_card(nl_data_mode=True)
        words = card._next_tx_words()
        self.assertEqual(card.lapm.taken, 29)
        self.assertEqual(card._nl_tx_bits, [])
        # 0,1,0,1... from the endpoint, oldest bit at TXD0 bit 0.
        self.assertEqual(words[0], 0xAAAA)

    def test_an_observed_indication_moves_transmit_onto_nl(self):
        card = self._tx_card(nl_data_mode=True)
        card._nl_rx_seen = True
        words = card._next_tx_words()
        self.assertEqual(card.lapm.taken, 29)
        self.assertEqual(len(card._nl_tx_bits), 29)
        self.assertEqual(words, (0xFFFF, 0xFFFF, 0xFFFF))   # mark fill

    def test_force_diverts_without_waiting_for_an_indication(self):
        card = self._tx_card(nl_data_mode=True, nl_data_forced=True)
        words = card._next_tx_words()
        self.assertEqual(len(card._nl_tx_bits), 29)
        self.assertEqual(words, (0xFFFF, 0xFFFF, 0xFFFF))

    def test_the_synchronous_state_is_latched_not_retested(self):
        # DM(0x3FC2) does not sit still at or above 0xC6 on an established
        # link, but the DSP transmits a datagram every time it asks for one.
        # Re-testing per datagram put mark fill inside the LAPM stream for 27%
        # of a live call and shredded every HDLC frame.
        card = self._tx_card(nl_data_mode=False)
        card._lapm_active = False
        card.dm[DATASTATE] = 0x00C6
        card._next_tx_words()
        self.assertTrue(card._lapm_active)
        self.assertEqual(card.tx_fill_datagrams, 0)
        taken = card.lapm.taken
        card.dm[DATASTATE] = 0x00C0          # the link is still up
        card._next_tx_words()
        self.assertEqual(card.tx_fill_datagrams, 0)
        self.assertGreater(card.lapm.taken, taken)

    def test_a_transient_zero_rate_word_does_not_punch_a_hole(self):
        card = self._tx_card(nl_data_mode=False)
        card.dm[DATASTATE] = 0x00C6
        card._next_tx_words()
        card.dm[0x3F61] = 0x0000             # rate word momentarily unreadable
        card._next_tx_words()
        self.assertEqual(card.tx_fill_datagrams, 0)
        self.assertEqual(card.lapm.taken, 29 * 2)

    def test_nothing_is_transmitted_before_the_pump_reaches_sync(self):
        card = self._tx_card(nl_data_mode=False)
        card._lapm_active = False
        card.dm[DATASTATE] = 0x0040
        card._next_tx_words()
        self.assertFalse(card._lapm_active)
        self.assertEqual(card.tx_payload_datagrams, 0)
        self.assertEqual(card.lapm.taken, 0)

    def test_the_synchronous_path_always_transmits_on_the_mailbox(self):
        card = self._tx_card(nl_data_mode=False)
        card._nl_rx_seen = True
        words = card._next_tx_words()
        self.assertEqual(card._nl_tx_bits, [])
        self.assertEqual(words[0], 0xAAAA)


@unittest.skipIf(shim_module is None, 'eicon_mips_shim needs unicorn')
class V22DatagramWidthTests(unittest.TestCase):
    """Page 1's width is a constant, because V.22bis negotiates none.

    Session 183 measured it: the V.22 overlay publishes every receive word as
    0xf000 -- four bits, left aligned, under the oldest-bit-at-15 convention --
    and never writes RXD1, while the transmit side reads TXD0 alone. There is
    no DATASTATE word to resolve on this page, and DM(0x3FC2) holds whatever
    the previous page left behind, so reading it would be worse than useless.
    """

    def _v22_card(self, bootpage=shim_module.V22_BOOTPAGE, **kwargs):
        card = _card(resident=shim_module.V22_OVERLAY, nl_data_mode=False,
                     lapm=_CountingLapm(pattern=(1,)), **kwargs)
        card.dm[0x3FAD] = 0
        card.dm[0x3F62] = 0            # the stale V.34 rate word
        # Overlay 0x0266 is resident for V.22 *and* V.32, so the bootpage is
        # what names the modulation and these tests have to state which one
        # they mean. They did not before, because the width was the same
        # constant either way -- which was the bug (Session 188f).
        card.dm[0x3FB0] = bootpage
        return card

    def test_the_transmit_width_is_four_bits(self):
        card = self._v22_card(lapm_active=False)
        card._next_tx_words()
        self.assertEqual(card._tx_datagram_bits, 4)
        self.assertEqual(card.lapm.taken, 4)

    def test_the_datagram_occupies_txd0_alone(self):
        # The page reads DM(0x3F05) and never DM(0x3F06)/DM(0x3F07), so the
        # other two words must stay clear.
        card = self._v22_card(lapm_active=False)
        words = card._next_tx_words()
        self.assertEqual(words[1:], (0, 0))
        # Four ones left aligned, the rest mark fill: 0xffff either way, so
        # assert the placement with a pattern that can tell them apart.
        card = self._v22_card(lapm_active=False)
        card.lapm = _CountingLapm(pattern=(0,))
        words = card._next_tx_words()
        self.assertEqual(words[0], 0x0FFF)

    def test_the_rate_is_published_as_2400_symmetric(self):
        card = self._v22_card(lapm_active=False)
        card._next_tx_words()
        self.assertTrue(card._lapm_active)
        self.assertEqual(card.negotiated_downstream_bps, 2400)
        self.assertEqual(card.negotiated_upstream_bps, 2400)

    def test_the_receive_width_is_four_bits_on_page_one_only(self):
        card = self._v22_card()
        self.assertEqual(card._rx_datagram_bits(), 4)
        # 0x2028 is a V.34 DATASTATESpeed word; off page 1 nothing changes.
        card.resident = 0x0261
        card.dm[0x3F62] = 0x2028
        self.assertEqual(card._rx_datagram_bits(), card._v34_rx_bits())

    def test_page_two_gets_the_v32_width_not_the_v22_one(self):
        # The same overlay, the other bootpage. Before Session 188f this
        # returned 4 for V.32 as well, so the pump framed a 2400 bit/s stream
        # out of a V.32 link and LAPM never established.
        card = self._v22_card(bootpage=shim_module.V32_BOOTPAGE,
                              lapm_active=False)
        card._next_tx_words()
        self.assertEqual(card._tx_datagram_bits, shim_module.V32_DATAGRAM_BITS)
        self.assertEqual(card._rx_datagram_bits(),
                         shim_module.V32_DATAGRAM_BITS)

    def test_page_two_publishes_its_own_rate(self):
        # Both modulations are 2400 baud, so the rate is the width times the
        # symbol rate rather than V.22bis's flat 2400.
        card = self._v22_card(bootpage=shim_module.V32_BOOTPAGE,
                              lapm_active=False)
        card._next_tx_words()
        expected = shim_module.V32_DATAGRAM_BITS * 2400
        self.assertEqual(card.negotiated_downstream_bps, expected)
        self.assertEqual(card.negotiated_upstream_bps, expected)

    def test_an_unknown_bootpage_keeps_the_v22_width(self):
        # Only page 2 is V.32; anything else on this overlay keeps the width
        # the code used before, so a page nobody has characterised cannot
        # silently acquire a six-bit datagram.
        card = self._v22_card(bootpage=0x0007, lapm_active=False)
        card._next_tx_words()
        self.assertEqual(card._tx_datagram_bits, 4)

    def test_the_constant_does_not_leak_onto_other_pages(self):
        card = self._v22_card(lapm_active=False)
        card.resident = 0x0258          # TIKRNL, no data pump at all
        card.dm[DATASTATE] = 0x00C6
        card._next_tx_words()
        self.assertFalse(card._lapm_active)
        self.assertIsNone(card._tx_datagram_bits)


if __name__ == '__main__':
    unittest.main()
