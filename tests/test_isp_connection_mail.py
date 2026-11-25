"""Account routing and mock usage-billing arithmetic."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile

SPEC = importlib.util.spec_from_file_location('connection_mail', Path(__file__).resolve().parents[1] / 'deploy/isp-billing/connection-mail.py')
mail = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mail)
TARIFF = dict(currency='NZD', cents_per_kb=7, cents_per_minute=10)
CONFIG = dict(sender='xtraisp@net.cryan.nz', recipient='fallback@net.cryan.nz', tariff=TARIFF,
              accounts={'scottc': {'recipient': 'snuggi@net.cryan.nz'}})


class ConnectionMailTests(unittest.TestCase):
    def test_rounds_combined_traffic_and_bundle_time_up(self):
        bill = mail.charges(dict(download_bytes=1000, upload_bytes=1, connected_at=100, ended_at=160.1), TARIFF)
        self.assertEqual(bill, dict(kb=2, minutes=2, data_cents=14, time_cents=20, total_cents=34))

    def test_exact_units_and_zero(self):
        self.assertEqual(mail.charges(dict(download_bytes=1000, upload_bytes=1000, connected_at=100, ended_at=160), TARIFF)['total_cents'], 24)
        self.assertEqual(mail.charges(dict(download_bytes=0, upload_bytes=0, connected_at=100, ended_at=100), TARIFF)['total_cents'], 0)

    def test_unknown_usage_does_not_become_free(self):
        self.assertIsNone(mail.charges(dict(download_bytes=None, upload_bytes=1, ended_at=200), TARIFF)['total_cents'])

    def test_account_recipient_override_and_fallback(self):
        self.assertEqual(mail.recipient_for({'account': 'scottc'}, CONFIG), 'snuggi@net.cryan.nz')
        self.assertEqual(mail.recipient_for({'account': 'other'}, CONFIG), 'fallback@net.cryan.nz')

    def test_per_account_tariff_override(self):
        config = {**CONFIG, 'accounts': {'other': {'recipient': 'other@net.cryan.nz', 'tariff': {'cents_per_kb': 9}}}}
        self.assertEqual(mail.tariff_for({'account': 'other'}, config)['cents_per_kb'], 9)
        self.assertEqual(mail.tariff_for({'account': 'other'}, config)['cents_per_minute'], 10)

    def test_queued_settings_remain_fixed(self):
        record = dict(id='test', interface='mtppp0', account='scottc', ended_at=160, connected_at=100,
                      download_bytes=1000, upload_bytes=1000, recipient='saved@net.cryan.nz', tariff=TARIFF)
        message = mail.message(record, CONFIG)
        self.assertEqual(message['To'], 'saved@net.cryan.nz')
        self.assertIn('MOCK TOTAL: NZD 0.24', message.get_content())
        self.assertIn('No payment is due', message.get_content())
        self.assertIn('PPP login: scottc', message.get_content())

    def test_rejects_header_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'config.json'
            config.write_text(json.dumps({**CONFIG, 'recipient': 'bad@host\nBcc: bad@host'}))
            with patch.object(mail, 'CONFIG', config), self.assertRaises(ValueError):
                mail.mail_settings()


if __name__ == '__main__':
    unittest.main()
