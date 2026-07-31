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
          tx_v42=True, lapm_active=True):
    card = object.__new__(shim_module.NativeMipsModem)
    card.shim = _Shim(connected)
    card.tx_v42 = tx_v42
    card._lapm_active = lapm_active
    card._nl_rx_seen = False
    card.idi_context = (0x1000, 0x2000, 0x3000)
    card.nl_entity_id = entity_id
    card.nl_data_queue = shim_module.collections.deque()
    card.nl_data_mode = nl_data_mode
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


if __name__ == '__main__':
    unittest.main()
