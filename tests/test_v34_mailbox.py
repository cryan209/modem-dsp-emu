import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v34_mailbox import (V34Mailbox, claim_tx_mailbox,
                         claim_wide_rx_mailbox)


class MailboxTests(unittest.TestCase):
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
        self.dm[0x3F61] = 0x10  # 24000, ten bits
        self.dm[0x3F62] = 0x1111  # 26400, eleven bits
        self.dm[0x3FAD] = 0xE000
        self.pump.before_sample()

    def test_training_fill_does_not_clock_lapm(self):
        self.dm[0x3FAD] = 0x8000
        self.pump.before_sample()
        self.assertEqual(self.dm[0x3F05], 0xFFFF)
        self.link.take.assert_not_called()

    def test_tx_waits_for_dsp_ack_and_packs_msb_first(self):
        self.start()
        self.assertEqual(self.dm[0x3F05], 0xAABF)
        self.assertEqual(self.dm[0x3FAD], 0x8000)
        self.pump.before_sample()
        self.link.take.assert_called_once_with(10)
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


class V90MailboxTests(unittest.TestCase):
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
