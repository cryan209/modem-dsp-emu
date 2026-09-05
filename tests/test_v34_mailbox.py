import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v34_mailbox import V34Mailbox, claim_tx_mailbox


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
