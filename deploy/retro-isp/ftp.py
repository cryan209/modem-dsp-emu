#!/usr/bin/python3
"""Private FTP uploads into isolated personal web directories."""
import hashlib
import hmac
import json
import logging
from pathlib import Path
from pyftpdlib.authorizers import DummyAuthorizer, AuthenticationFailed
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

CONFIG = Path('/etc/retro-isp/ftp-users.json')


class Authorizer(DummyAuthorizer):
    def __init__(self):
        super().__init__()
        self.records = json.loads(CONFIG.read_text())
        for name in self.records:
            self.add_user(name, '', '/srv/retro-isp/users/'+name, perm='elradfmwMT')

    def validate_authentication(self, username, password, handler):
        record = self.records.get(username)
        if not record:
            raise AuthenticationFailed('Incorrect username or password')
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record['salt']), n=16384, r=8, p=1).hex()
        if not hmac.compare_digest(value, record['hash']):
            raise AuthenticationFailed('Incorrect username or password')


class PersonalFTPHandler(FTPHandler):
    def on_connect(self):
        # A newly registered account works on its next connection; no FTP restart.
        self.authorizer = Authorizer()


def main():
    logging.basicConfig(level=logging.WARNING)
    handler = PersonalFTPHandler
    handler.authorizer = Authorizer()
    handler.banner = 'Xtra ISP personal web-space FTP. Welcome to the information superhighway.'
    handler.passive_ports = range(50000, 50010)
    handler.masquerade_address = '10.150.150.157'
    handler.max_login_attempts = 3
    handler.timeout = 300
    server = FTPServer(('10.150.150.157', 21), handler)
    server.max_cons = 32
    server.max_cons_per_ip = 8
    server.serve_forever()


if __name__ == '__main__':
    main()
