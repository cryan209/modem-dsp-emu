from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy/isp-registration'))
from request_origin import same_origin

class OriginTests(unittest.TestCase):
    def test_account_form_preserves_same_origin(self):
        import account_web
        from unittest.mock import Mock
        handler = object.__new__(account_web.Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        handler.respond(b"form")
        handler.send_header.assert_any_call("Referrer-Policy", "same-origin")

    def test_own_addresses(self):
        for host in ('10.150.150.157','eicon420.net.cryan.nz','eicon420'):
            self.assertTrue(same_origin('http://'+host+':8087',host+':8087',8087))
        self.assertTrue(same_origin(None,'10.150.150.157:8087',8087))
    def test_rejects_other_origins(self):
        for origin in ('http://10.150.150.157:8086','http://evil.example:8087','null','http://eicon420.net.cryan.nz:8087','https://10.150.150.157:8087','http://10.150.150.157:8087@evil.example:8087'):
            self.assertFalse(same_origin(origin,'10.150.150.157:8087',8087),origin)
    def test_rejects_untrusted_host(self):
        self.assertFalse(same_origin('http://evil.example:8087','evil.example:8087',8087))

if __name__=='__main__':unittest.main()
