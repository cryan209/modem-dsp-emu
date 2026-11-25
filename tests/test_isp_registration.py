import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('provision',Path(__file__).resolve().parents[1]/'deploy/isp-registration/provision.py')
p=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(p)


class ProvisionTests(unittest.TestCase):
    def test_pop3_lifecycle_without_context_manager(self):
        calls=[]
        client=SimpleNamespace(user=lambda value:calls.append('user'),pass_=lambda value:calls.append('pass'),quit=lambda:calls.append('quit'))
        with patch.object(p.poplib,'POP3',return_value=client):p.verify_mail('newuser','valid-password-123')
        self.assertEqual(calls,['user','pass','quit'])

    def test_input_validation(self):
        for name in ('root','../bad','ab','mail','user name'):
            with self.assertRaises(p.RegistrationError):p.validate(dict(username=name,password='valid-password-123',password2='valid-password-123'))
        with self.assertRaises(p.RegistrationError):p.validate(dict(username='validname',password='bad\npassword',password2='bad\npassword'))
        user,password=p.validate(dict(username='NewUser',generate=True));self.assertEqual(user,'newuser');self.assertGreaterEqual(len(password),10)

    def test_password_length_boundaries(self):
        for length in (6, 9, 10, 64):
            password = 'a' * length
            self.assertEqual(p.validate(dict(username='newuser', password=password, password2=password))[1], password)
        for length in (0, 5, 65):
            password = 'a' * length
            with self.assertRaises(p.RegistrationError):
                p.validate(dict(username='newuser', password=password, password2=password))

    def test_pap_exact_entries_and_idempotence(self):
        original='* multitech-7910 "" 10.254.104.2\nother server "keepsecret" *\n'
        result=p.pap_account(original,'newuser','valid-password-123')
        self.assertTrue(result.startswith(original))
        entries=[shlex.split(line) for line in result.splitlines() if line.startswith('"newuser"')]
        self.assertEqual(len(entries),5)
        self.assertEqual(entries[0],['newuser','multitech-7910','valid-password-123','10.254.104.2'])
        self.assertEqual(result,p.pap_account(result,'newuser','valid-password-123'))

    def test_provision_duplicate_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths={'ROOT':root/'state','FTP':root/'ftp.json','PAP':root/'pap','BILLING':root/'billing.json','HOMES':root/'homes'}
            paths['HOMES'].mkdir();paths['FTP'].write_text('{}');paths['PAP'].write_text('* multitech-7910 "" 10.254.104.2\n');paths['BILLING'].write_text('{"accounts":{"existing":{"recipient":"keep@net.cryan.nz"}}}')
            with patch.multiple(p,**paths),patch.object(p,'mailbox_names',return_value=set()),patch.object(p,'create_mail') as create,patch.object(p.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())),patch.object(p.os,'chown'),patch.object(p.os,'fchown'),patch.object(p.hashlib,'scrypt',return_value=b'hashed-test-value',create=True):
                data=dict(username='newuser',password='valid-password-123',password2='valid-password-123')
                create.side_effect=OSError('temporary mail failure')
                with self.assertRaises(p.RegistrationError):p.provision(data)
                self.assertFalse((paths['HOMES']/'newuser').exists())
                self.assertNotIn('newuser',paths['PAP'].read_text())
                create.side_effect=None
                previous=os.umask(0o077)
                try:result=p.provision(data)
                finally:os.umask(previous)
                self.assertTrue(result['ok'])
                self.assertEqual((paths['HOMES']/'newuser').stat().st_mode & 0o777,0o755)
                ftp=json.loads(paths['FTP'].read_text());self.assertIn('newuser',ftp);self.assertNotIn('password',ftp['newuser'])
                billing=json.loads(paths['BILLING'].read_text());self.assertEqual(billing['accounts']['newuser']['recipient'],'newuser@net.cryan.nz');self.assertIn('existing',billing['accounts'])
                self.assertTrue((paths['HOMES']/'newuser/index.html').exists())
                with self.assertRaises(p.RegistrationError):p.provision(data)


if __name__=='__main__':unittest.main()
