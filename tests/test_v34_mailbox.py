import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v34_mailbox import (V34Mailbox, claim_tx_mailbox,
                         claim_wide_rx_mailbox)


class MailboxTests(unittest.TestCase):
    def test_v90_to_v34_retrain_updates_detection_clock_at_data_ready(self):
        self.card.resident = 0x026A
        self.dm[0x3FC2], self.dm[0x3FAD] = 0xD0, 0x8000
        self.dm[0x3F61], self.dm[0x3F62] = 0x2028, 0x11F3
        self.pump.before_sample()
        self.link.set_detection_clock.assert_called_once_with(8000 / 6)
        self.card.resident = 0x0261
        self.pump.pending = False
        self.dm[0x3FC2], self.dm[0x3FAD] = 0xC6, 0x8000
        self.dm[0x3F61], self.dm[0x3F62] = 0x0011, 0x1112
        self.pump.before_sample()
        self.assertEqual(self.link.set_detection_clock.call_count, 1)
        self.dm[0x3FC2], self.dm[0x3FC0] = 0xD0, 0x0500
        self.dm[0x14AE] = 1
        self.pump.before_sample()
        self.link.set_detection_clock.assert_called_with(2400)
        self.assertEqual(self.link.set_detection_clock.call_count, 2)

    def test_claim_checks_both_task_layouts_before_mutating(self):
        for offsets in ((0, 0x62, 0x64, 0x68, 0x70),
                        (0, 0x63, 0x65, 0x69, 0x71)):
            pm = [0x123456] * 0x4000
            for offset, opcode in zip(offsets,
                    (0x93F05A, 0x93F05F, 0x93F05F, 0x93F06F, 0x93F07F)):
                pm[0x600 + offset] = opcode
            good = pm.copy()
            pm[0x600 + offsets[-1]] = 0
            before = pm.copy()
            with self.assertRaises(RuntimeError):
                claim_tx_mailbox(pm)
            self.assertEqual(pm, before)
            claim_tx_mailbox(good)
            self.assertEqual([i for i, value in enumerate(good) if value == 0],
                             [0x600 + offset for offset in offsets])

    def setUp(self):
        self.card = SimpleNamespace(dm=[0] * 0x4000, resident=0x0261)
        self.dm = self.card.dm
        self.link = Mock()
        self.link.take.side_effect = lambda count: ([1, 0] * 8)[:count]
        self.pump = V34Mailbox(self.card, self.link)

    def start(self):
        self.dm[0x3FC2] = 0xD0
        self.dm[0x3FC0] = 0x0500  # CTS/RFS and DCD/RSD
        self.dm[0x3F61] = 0x10  # 24000, ten bits
        self.dm[0x3F62] = 0x1111  # 26400, eleven bits
        self.dm[0x3FAD] = 0xE000
        self.pump.before_sample()

    def test_training_fill_does_not_clock_lapm(self):
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertEqual(self.dm[0x3F05], 0xFFFF)
        self.link.take.assert_not_called()

    def test_v34_detection_waits_for_cts_and_dcd_in_data(self):
        self.dm[0x3F61], self.dm[0x3F62] = 0x10, 0x1111
        for state, status in ((0xC6, 0x0500), (0xD0, 0x0400),
                              (0xD0, 0x0100)):
            self.dm[0x3FC2], self.dm[0x3FC0] = state, status
            self.dm[0x3FAD] = 0x8000
            self.pump.before_sample()
            self.pump.after_sample()
            self.assertFalse(self.pump.active)
        self.link.take.assert_not_called()
        self.dm[0x3FC0] = 0x0500
        self.dm[0x14AE] = 1
        self.pump.before_sample()
        self.assertTrue(self.pump.active)
        self.link.take.assert_called_once_with(10)

    def test_tx_waits_for_dsp_ack_and_packs_msb_first(self):
        self.start()
        self.assertEqual(self.dm[0x3F05], 0xAABF)
        self.assertEqual(self.dm[0x3FAD], 0x8000)
        self.pump.before_sample()
        self.link.take.assert_called_once_with(10)
        self.link.set_detection_clock.assert_called_once_with(2400)
        self.dm[0x3FAD] = 0
        self.pump.after_sample()
        self.assertEqual(self.pump.tx_accepted, 1)
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertEqual(self.link.take.call_count, 2)

    def test_receive_order_width_and_acknowledgement(self):
        self.start()
        self.dm[0x3FAD] = 0x6000
        self.dm[0x3FAE], self.dm[0x3FAF] = 0x8000, 0x0020
        self.pump.after_sample()
        self.assertEqual(self.link.feed.call_args_list[0].args[0], [1] + [0] * 10)
        self.assertEqual(self.link.feed.call_args_list[1].args[0], [0] * 10 + [1])
        self.assertEqual(self.dm[0x3FAD], 0)
        self.assertEqual(self.pump.rx_datagrams, 2)

    def test_v34_consumes_each_scheduler_step_while_f_stays_set(self):
        self.start()
        self.link.take.reset_mock()
        # Recorded PRI samples: F remains set across 2 -> 1 -> 0, then
        # clears at the period boundary. Repeated snapshots are not requests.
        for token, request in ((2, 1), (2, 1), (1, 1), (1, 1),
                               (0, 1), (0, 0), (2, 1)):
            self.dm[0x14AE] = token
            self.dm[0x3FAD] = 0x8000 if request else 0
            self.pump.before_sample()
            self.pump.after_sample()
        self.assertEqual(self.link.take.call_count, 4)

    def test_v34_new_words_follow_scheduler_despite_no_falling_edge(self):
        self.link.take.side_effect = ([0] * 10, [1] * 10, [0] * 10)
        self.dm[0x14AE] = 2
        self.start()
        words = [self.dm[0x3F05]]
        for token in (1, 0):
            self.dm[0x14AE] = token
            self.pump.before_sample()
            words.append(self.dm[0x3F05])
        self.assertEqual(words, [0x003f, 0xffff, 0x003f])

    def test_transient_speed_words_keep_negotiated_width(self):
        self.start()
        self.dm[0x3FAD] = 0
        self.pump.after_sample()
        self.dm[0x3F61] = self.dm[0x3F62] = 0
        self.dm[0x3FC2] = 0xC2
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.link.take.assert_called_with(10)

    def test_other_overlay_is_never_serviced(self):
        self.start()
        self.card.resident = 0x0260
        self.dm[0x3F05] = 0x1234
        self.pump.before_sample()
        self.pump.after_sample()
        self.assertEqual(self.dm[0x3F05], 0x1234)
        self.link.line_disturbed.assert_called()

    def test_fast_retrain_holds_lapm_until_data_receiver_is_ready(self):
        self.start()
        self.link.reset_mock()
        # Captured fast-retrain path never goes below B0. C6 is still
        # training, and DATA alone does not establish receiver readiness.
        for state, status in ((0xBD, 0x0500), (0xC6, 0x0500),
                              (0xCC, 0x0500), (0xD0, 0x0100)):
            with self.subTest(state=state, status=status):
                self.dm[0x3FC2], self.dm[0x3FC0] = state, status
                self.pump.before_sample()
                self.link.line_disturbed.assert_called_with('V.34 retrain')
                self.link.line_restored.assert_not_called()
        self.dm[0x3FC0] = 0x0500
        self.pump.before_sample()
        self.link.line_restored.assert_called_once_with('synchronous state')

    def test_fast_retrain_preserves_real_lapm_unacknowledged_payload(self):
        from v42_lapm import LapmEndpoint, encode_frame
        link = LapmEndpoint(log=lambda _: None, detect=False, n401=4,
                            window=3, poll_after=2, retransmit_after=4,
                            n400=3)
        self.pump = V34Mailbox(self.card, link)
        self.start()
        link.feed(encode_frame(b'\x03\x7f'))
        link.send(b'ABCDEFGH')
        link.take(64)
        outstanding = tuple(link.unacked)
        self.assertTrue(outstanding)
        for state in (0xBD, 0xC6, 0xCC):
            self.dm[0x3FC2] = state
            # Far longer than this endpoint's retry budget. A physical
            # retrain cannot consume the sequenced frames or reset the link.
            for token in range(100):
                self.dm[0x14AE] = token % 3
                self.dm[0x3FAD] = 0x8000
                self.pump.before_sample()
            self.assertTrue(link.suspended)
            self.assertTrue(link.connected)
            self.assertIsNone(link.failed)
            self.assertEqual(tuple(link.unacked), outstanding)
        self.dm[0x3FC2] = 0xD0
        self.pump.before_sample()
        self.assertFalse(link.suspended)
        self.assertEqual(tuple(link.unacked), outstanding)


class V32MailboxTests(unittest.TestCase):
    setUp = MailboxTests.setUp

    def test_v32_waits_for_negotiated_rate_and_host_request(self):
        self.card.resident = 0x0266
        self.dm[0x3FB0] = 2
        self.dm[0x3FC2] = 0xEA  # LEC does not use the V.34 DATA ladder
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertFalse(self.pump.active)
        self.dm[0x3FAD] = 0
        self.pump.after_sample()
        self.dm[0x3F62] = 0x11AA
        self.pump.before_sample()
        self.assertFalse(self.pump.active)
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertTrue(self.pump.active)
        self.link.take.assert_called_once_with(4)
        self.link.set_detection_clock.assert_called_once_with(2400)
        self.assertEqual(self.card.negotiated_downstream_bps, 9600)
        self.assertEqual(self.dm[0x3FAD] & 0x8000, 0)
        self.assertFalse(self.pump.pending)
        self.dm[0x3FAD] = 0x2000
        self.dm[0x3FAE] = 0xA000
        self.pump.after_sample()
        self.link.feed.assert_called_once_with([1, 0, 1, 0])
        self.dm[0x3F62] = 0x11A9
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.link.take.assert_called_with(3)
        self.link.line_disturbed.assert_called_with('rate change')
        # A different bootpage in the shared image must stop servicing V.32.
        self.dm[0x3FB0] = 1
        self.dm[0x3FAD] = 0xE000
        self.link.take.reset_mock()
        self.link.feed.reset_mock()
        self.pump.before_sample()
        self.pump.after_sample()
        self.link.take.assert_not_called()
        self.link.feed.assert_not_called()

    def test_shared_overlay_does_not_guess_v22_or_training_width(self):
        self.card.resident = 0x0266
        self.dm[0x3FC2] = 0xD0
        self.dm[0x3FC0] = 0x0500
        for bootpage, speed in ((1, 0x11AA), (2, 0), (2, 0x1113)):
            self.dm[0x3FB0], self.dm[0x3F62] = bootpage, speed
            self.dm[0x3FAD] = 0x8000
            self.pump.before_sample()
            self.dm[0x3FAD] = 0
            self.pump.after_sample()
            self.assertFalse(self.pump.active)
        self.link.take.assert_not_called()

    def test_v32_rate_range_and_directional_widths(self):
        self.card.resident = 0x0266
        self.dm[0x3FB0] = 2
        for speed, width in ((0x01A8, 2), (0x11A9, 3), (0x11AA, 4),
                             (0x11AB, 5), (0x11AC, 6)):
            self.dm[0x3F61] = 0
            self.dm[0x3F62] = speed
            self.assertEqual(self.pump._widths(), (width, width))
        self.dm[0x3F61], self.dm[0x3F62] = 0x000A, 0x11AC
        self.assertEqual(self.pump._widths(), (4, 6))
        self.dm[0x3F61] = 0x0013  # 31200 is outside V.32bis
        self.assertEqual(self.pump._widths(), (None, 6))


class V90MailboxTests(unittest.TestCase):
    def test_v90d_detection_does_not_spend_t400_during_the_training_tail(self):
        self.dm[0x3F61], self.dm[0x3F62] = 0x2028, 0x11F3
        for state in (0xC6, 0xC8, 0xCA, 0xCC):
            self.dm[0x3FC2], self.dm[0x3FAD] = state, 0x8000
            self.pump.pending = False
            self.pump.before_sample()
            self.assertFalse(self.pump.active)
            self.link.take.assert_not_called()
        self.dm[0x3FC2] = 0xD0
        self.pump.pending = False
        self.pump.before_sample()
        self.assertTrue(self.pump.active)
        self.link.take.assert_called_once_with(29)

    def setUp(self):
        self.card = SimpleNamespace(dm=[0] * 0x4000, resident=0x026A)
        self.dm = self.card.dm
        self.link = Mock()
        self.link.take.side_effect = lambda count: [1, 0, 0] * 16
        self.pump = V34Mailbox(self.card, self.link)

    def test_v90d_downstream_is_wide_lsb_first_across_three_words(self):
        self.dm[0x3FC2] = 0xD0
        self.dm[0x3F61] = 0x2031      # V.90 table (bit 5), 21 + 17 = 38 bits
        self.dm[0x3F62] = 0x11F3      # V.34 upstream 31200, 13 bits
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertEqual((self.pump.tx_width, self.pump.rx_width), (38, 13))
        self.link.take.assert_called_once_with(38)
        self.link.set_detection_clock.assert_called_once_with(8000 / 6)
        bits = ([1, 0, 0] * 16)[:38] + [1] * 10
        words = [sum(bits[w * 16 + i] << i for i in range(16)) for w in range(3)]
        self.assertEqual([self.dm[0x3F05], self.dm[0x3F06], self.dm[0x3F07]],
                         words)
        self.assertEqual(self.card.negotiated_downstream_bps, 50667)
        self.assertEqual(self.card.negotiated_upstream_bps, 31200)

    def test_v90a_downstream_receive_spans_rxd0_rxd1_rxd2(self):
        self.card.resident = 0x026B
        self.dm[0x3FC2] = 0xD0
        self.dm[0x3F61] = 0x0013      # V.34 upstream 31200, 13 bits
        self.dm[0x3F62] = 0x21F1      # V.90 table (bit 13), 38 bits
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertEqual((self.pump.tx_width, self.pump.rx_width), (13, 38))
        self.link.set_detection_clock.assert_called_once_with(2400)
        self.assertEqual(self.card.negotiated_downstream_bps, 50667)
        self.assertEqual(self.card.negotiated_upstream_bps, 31200)
        self.dm[0x3FAD] = 0x2000
        self.dm[0x3FAE], self.dm[0x3FAF], self.dm[0x3FB1] = 0x8001, 0x0000, 0xFC00
        self.pump.after_sample()
        fed = self.link.feed.call_args.args[0]
        self.assertEqual(len(fed), 38)
        self.assertEqual(fed[:16], [1] + [0] * 14 + [1])
        self.assertEqual(fed[16:32], [0] * 16)
        self.assertEqual(fed[32:], [1] * 6)
        self.assertEqual(self.dm[0x3FAD] & 0x2000, 0)
        self.assertEqual(self.pump.rx_datagrams, 1)


class WideRxClaimTests(unittest.TestCase):
    def test_suppresses_only_the_kernel_clear_store(self):
        pm = [0x123456] * 0x4000
        pm[0x07B5:0x07B8] = [0x4DFFFA, 0x23820F, 0x93FADA]
        self.assertEqual(claim_wide_rx_mailbox(pm), 0x07B7)
        self.assertEqual(pm[0x07B5:0x07B8], [0x4DFFFA, 0x23820F, 0])

    def test_kernel_without_the_path_is_left_alone(self):
        pm = [0x123456] * 0x4000
        before = pm.copy()
        self.assertIsNone(claim_wide_rx_mailbox(pm))
        self.assertEqual(pm, before)


class V90aActivationTests(unittest.TestCase):
    def test_caller_waits_for_data_state_before_starting_lapm(self):
        card = SimpleNamespace(dm=[0] * 0x4000, resident=0x026B)
        link = Mock()
        link.take.side_effect = lambda count: [1] * count
        pump = V34Mailbox(card, link)
        card.dm[0x3F61], card.dm[0x3F62] = 0x0013, 0x21E4
        card.dm[0x3FC2], card.dm[0x3FAD] = 0xC6, 0x8000
        pump.before_sample()
        self.assertFalse(pump.active)
        link.take.assert_not_called()
        pump.pending = False
        card.dm[0x3F62], card.dm[0x3FC2] = 0x21F1, 0xD0
        pump.before_sample()
        self.assertTrue(pump.active)
        self.assertEqual(pump.rx_width, 38)



class V90aTxScheduleTests(unittest.TestCase):
    def test_caller_supplies_once_per_scheduler_step(self):
        card = SimpleNamespace(dm=[0] * 0x4000, resident=0x026B)
        link = Mock()
        link.take.side_effect = lambda count: [0] * count
        pump = V34Mailbox(card, link)
        card.dm[0x3F61], card.dm[0x3F62], card.dm[0x3FC2] = 0x0013, 0x21F1, 0xD0
        # (1213, 1214, F) per bearer sample, as measured in data mode.
        trace = [(3, 2, 1), (3, 2, 1), (2, 1, 1), (2, 1, 1), (2, 1, 1),
                 (1, 0, 1), (1, 0, 1), (0, 0, 0), (0, 0, 0), (0, 0, 0)] * 2
        for count, left, flag in trace:
            card.dm[0x1213], card.dm[0x1214] = count, left
            card.dm[0x3FAD] = 0x8000 if flag else 0
            pump.before_sample()
            pump.after_sample()
        self.assertEqual(link.take.call_count, 6)   # three per period
