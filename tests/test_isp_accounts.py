import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy/isp-registration'))
import accounts as a


class AccountTests(unittest.TestCase):
    def setUp(self):a.SESSIONS.clear();a.ATTEMPTS.clear()
    def login(self):
        with patch.object(a,'authenticate',return_value=True):return a.handle(dict(action='login',username='alice',password='not-used',address='10.0.0.2'))
    def test_session_scope_expiry_and_csrf(self):
        login=self.login();request=dict(action='profile',session=login['session'],address='10.0.0.2',username='bob')
        with patch.object(a,'profile',return_value={'username':'alice'}) as profile:
            self.assertEqual(a.handle(request)['username'],'alice');profile.assert_called_once_with('alice')
        with self.assertRaises(a.AccountError):a.session({**request,'address':'10.0.0.3'})
        with self.assertRaises(a.AccountError):a.session(request,True)
        a.SESSIONS[login['session']]['expires']=time.time()-1
        with self.assertRaises(a.AccountError):a.session(request)
    def test_billing_email_isolated_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);billing=root/'billing.json';billing.write_text('{"accounts":{"bob":{"recipient":"bob@net.cryan.nz"}}}')
            settings=root/'portal.json';settings.write_text('{}')
            with patch.object(a.p,'ROOT',root),patch.object(a.p,'BILLING',billing),patch.object(a,'SETTINGS',settings):
                login=self.login();request=dict(action='billing_email',session=login['session'],csrf=login['csrf'],address='10.0.0.2',email='alice@net.cryan.nz')
                self.assertTrue(a.handle(request)['ok']);data=json.loads(billing.read_text());self.assertEqual(data['accounts']['bob']['recipient'],'bob@net.cryan.nz');self.assertEqual(data['accounts']['alice']['recipient'],'alice@net.cryan.nz')
                with self.assertRaises(a.AccountError):a.handle({**request,'email':'a@b\r\nBcc: somebody'})
    def test_failed_login_and_logout(self):
        with patch.object(a,'authenticate',return_value=False),self.assertRaises(a.AccountError):a.handle(dict(action='login',username='alice',password='bad',address='10.0.0.2'))
        login=self.login();self.assertTrue(a.handle(dict(action='logout',session=login['session'],csrf=login['csrf'],address='10.0.0.2'))['ok']);self.assertFalse(a.SESSIONS)


if __name__=='__main__':unittest.main()
