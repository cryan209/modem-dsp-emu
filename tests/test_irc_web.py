import importlib.util
from pathlib import Path
import sys
import socket
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import build_opener, HTTPCookieProcessor
from urllib.error import HTTPError
from urllib.parse import urlencode
from http.cookiejar import CookieJar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy/isp-registration'))
spec = importlib.util.spec_from_file_location('irc_web', Path(__file__).resolve().parents[1]/'deploy/retro-isp/irc_web.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class ChatTests(unittest.TestCase):
    def test_html_client_with_irc_peer(self):
        client, server = socket.socketpair()
        commands = []
        def peer():
            with server, server.makefile('rb') as source:
                for raw in source:
                    line = raw.decode().strip()
                    commands.append(line)
                    if line.startswith('USER '):
                        server.sendall(b':irc 001 alice :Welcome\r\nPING :test\r\n')
                    if line.startswith('JOIN '):
                        server.sendall(b':alice!web@host JOIN :#xtra\r\n:other!x@y PRIVMSG #xtra :<script>alert(1)</script>\r\n')
        threading.Thread(target=peer, daemon=True).start()
        http = w.ThreadingHTTPServer(('127.0.0.1', 0), w.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(http.server_port) + '/'
        jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        try:
            response = opener.open(base)
            self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
            response.read()
            token = next(iter(jar)).value
            with patch.object(w, 'connect', return_value=client):
                opener.open(base, urlencode(dict(action='Join', nick='alice', channel='#xtra', token=token)).encode()).read()
            for _ in range(100):
                chat = next(iter(w.SESSIONS.values()))
                if chat.joined and any('<script>' in line for line in chat.lines):
                    break
                time.sleep(.01)
            body = opener.open(base).read().decode()
            self.assertIn('&lt;script&gt;', body)
            self.assertNotIn('<script>', body)
            self.assertIn('PONG :test', commands)
            with self.assertRaises(HTTPError) as error:
                opener.open(base, urlencode(dict(action='Send', message='bad', token='wrong')).encode())
            self.assertEqual(error.exception.code, 403)
            opener.open(base, urlencode(dict(action='Send', message='hello IRC', token=chat.csrf)).encode()).read()
            for _ in range(100):
                if 'PRIVMSG #xtra :hello IRC' in commands:
                    break
                time.sleep(.01)
            self.assertIn('PRIVMSG #xtra :hello IRC', commands)
            with self.assertRaises(ValueError):
                w.clean('hello\r\nOPER root', r'[^\x00-\x1f\x7f]{1,350}')
            chat.last -= w.TTL + 1
            w.reap()
            self.assertTrue(chat.closed)
            self.assertFalse(w.SESSIONS)
        finally:
            for chat in list(w.SESSIONS.values()):
                chat.close()
            w.SESSIONS.clear()
            http.shutdown()
            http.server_close()


if __name__ == '__main__':
    unittest.main()
