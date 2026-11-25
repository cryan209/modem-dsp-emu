#!/usr/bin/python3
"""Bounded, in-memory IRC bridge for JavaScript-free browsers."""
from collections import deque
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import socket
import threading
import time
import re
from urllib.parse import parse_qs, urlsplit
from request_origin import same_origin

connect = socket.create_connection
HOST = '10.150.150.157'
CHANNELS = ('#xtra', '#helpdesk')
SESSIONS = {}
LOCK = threading.RLock()
TTL = 900


def clean(value, pattern):
    if not re.fullmatch(pattern, value):
        raise ValueError('Please check your nickname or message.')
    return value


class Chat:
    def __init__(self, nick, channel, address):
        self.nick, self.channel, self.address = nick, channel, address
        self.csrf = secrets.token_urlsafe(32)
        self.lines = deque(maxlen=100)
        self.last = time.monotonic()
        self.closed = False
        self.joined = False
        self.send_lock = threading.Lock()
        self.sock = connect((HOST, 6667), timeout=5)
        self.sock.settimeout(30)
        self.send('NICK ' + nick)
        self.send('USER webchat 0 * :Retro HTML chat')
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, line):
        with self.send_lock:
            self.sock.sendall((line + '\r\n').encode('utf-8'))

    def close(self):
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def read(self):
        pending = b''
        try:
            while not self.closed:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 16384:
                    break
                while b'\n' in pending:
                    raw, pending = pending.split(b'\n', 1)
                    line = raw.rstrip(b'\r').decode('utf-8', 'replace')
                    if line.startswith('PING '):
                        self.send('PONG ' + line[5:])
                        continue
                    prefix = ''
                    if line.startswith(':'):
                        prefix, _, line = line[1:].partition(' ')
                    command, _, rest = line.partition(' ')
                    who = prefix.split('!')[0]
                    if command == '001':
                        self.nick = rest.split(' ')[0]
                        self.send('JOIN ' + self.channel)
                    elif command == '433':
                        self.lines.append('Nickname already in use. Disconnect and choose another.')
                        self.close()
                    elif command == 'PRIVMSG':
                        target, _, message = rest.partition(' :')
                        self.lines.append('<' + who + '> ' + message)
                    elif command == 'JOIN':
                        if who == self.nick:
                            self.joined = True
                        self.lines.append('* ' + who + ' joined ' + rest.lstrip(':'))
                    elif command in ('PART', 'QUIT', 'NICK', 'KICK'):
                        self.lines.append('* ' + who + ' ' + command.lower() + ' ' + rest)
                        if command == 'KICK' and rest.split(' ')[:2] == [self.channel, self.nick]:
                            self.joined = False
                    elif command in ('332', '353') or (command.isdigit() and int(command) >= 400):
                        self.lines.append(rest)
        except OSError:
            pass
        finally:
            self.close()
            self.lines.append('* Disconnected from IRC.')


def reap():
    with LOCK:
        for key, chat in list(SESSIONS.items()):
            if time.monotonic() - chat.last > TTL:
                chat.close()
                del SESSIONS[key]


def janitor():
    while True:
        time.sleep(30)
        reap()


def document(content):
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN"><HTML><HEAD>'
            '<TITLE>Xtra IRC chat</TITLE></HEAD><BODY BGCOLOR="#FFFFFF" TEXT="#000000">'
            '<H1>Xtra IRC chat</H1><P><A HREF="http://' + HOST + ':8086/">ISP services</A></P>'
            + content + '</BODY></HTML>').encode('ascii', 'xmlcharrefreplace')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except Exception:
            return None, None
        key = cookies['retro_irc'].value if 'retro_irc' in cookies else ''
        with LOCK:
            chat = SESSIONS.get(key)
            if chat and chat.address == self.client_address[0]:
                chat.last = time.monotonic()
                return key, chat
        return None, None

    def reply(self, content, code=200, cookie=None):
        body = document(content)
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=us-ascii')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; frame-ancestors 'none'")
        if cookie is not None:
            self.send_header('Set-Cookie', 'retro_irc=' + cookie + '; Path=/; HttpOnly; SameSite=Strict')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path != '/':
            self.send_error(404)
            return
        reap()
        key, chat = self.session()
        if chat:
            with LOCK:
                lines = list(chat.lines)
            hidden = '<INPUT TYPE="hidden" NAME="token" VALUE="' + chat.csrf + '">'
            self.reply('<P>Nickname: <B>' + escape(chat.nick) + '</B> | Channel: <B>' + escape(chat.channel) + '</B></P>'
                       '<P><A HREF="/">Refresh messages</A> (refresh manually to keep your message draft).</P>'
                       '<PRE>' + escape('\n'.join(lines) or 'Connecting... Refresh shortly.') + '</PRE>'
                       '<FORM METHOD="POST" ACTION="/">' + hidden +
                       '<INPUT NAME="message" SIZE="60" MAXLENGTH="350">'
                       '<INPUT TYPE="submit" NAME="action" VALUE="Send"></FORM>'
                       '<FORM METHOD="POST" ACTION="/">' + hidden +
                       '<INPUT TYPE="submit" NAME="action" VALUE="Disconnect"></FORM>'
                       '<P>' + ('Connected.' if not chat.closed else 'Disconnected. Use Disconnect to return to the join form.') +
                       ' Idle sessions close after 15 minutes. Only the latest 100 events are kept in memory.</P>')
        else:
            token = secrets.token_urlsafe(32)
            self.reply('<P>Chat with IRC users in #xtra or #helpdesk. No JavaScript or IRC software needed. '
                       'Messages are visible to everyone in the channel. Cookies must be enabled.</P>'
                       '<FORM METHOD="POST" ACTION="/"><INPUT TYPE="hidden" NAME="token" VALUE="' + token + '">'
                       '<P>Nickname: <INPUT NAME="nick" MAXLENGTH="9"></P><P>Channel: <SELECT NAME="channel">'
                       '<OPTION>#xtra</OPTION><OPTION>#helpdesk</OPTION></SELECT></P>'
                       '<INPUT TYPE="submit" NAME="action" VALUE="Join"></FORM>', cookie=token)

    def do_POST(self):
        if urlsplit(self.path).path != '/':
            self.send_error(404)
            return
        self.connection.settimeout(10)
        try:
            if not same_origin(self.headers.get('Origin'), self.headers.get('Host', ''), 8088):
                self.send_error(403)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('Invalid form size.')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                raise ValueError('Use the chat form.')
            data = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
            value = lambda name: data.get(name, [''])[0]
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            supplied = value('token')
            reap()
            key, chat = self.session()
            expected = chat.csrf if chat else (cookies['retro_irc'].value if 'retro_irc' in cookies else '')
            if not expected or not secrets.compare_digest(expected, supplied):
                self.send_error(403)
                return
            action = value('action')
            cookie = None
            if action == 'Join' and not chat:
                nick = clean(value('nick'), r'[A-Za-z][A-Za-z0-9_-]{0,8}')
                channel = value('channel')
                if channel not in CHANNELS:
                    raise ValueError('Choose #xtra or #helpdesk.')
                with LOCK:
                    if len(SESSIONS) >= 6 or sum(c.address == self.client_address[0] for c in SESSIONS.values()) >= 2:
                        raise ValueError('Chat is full. Try again shortly.')
                    chat = Chat(nick, channel, self.client_address[0])
                    key = secrets.token_urlsafe(32)
                    SESSIONS[key] = chat
                    cookie = key
            elif action == 'Send' and chat:
                message = clean(value('message'), r'[^\x00-\x1f\x7f]{1,350}')
                if len(message.encode('utf-8')) > 350:
                    raise ValueError('Message is too long.')
                if chat.closed or not chat.joined:
                    raise ValueError('Not in the channel yet. Refresh or reconnect.')
                if time.monotonic() - getattr(chat, 'sent_at', float('-inf')) < 1:
                    raise ValueError('Please wait a second between messages.')
                chat.sent_at = time.monotonic()
                chat.send('PRIVMSG ' + chat.channel + ' :' + message)
                chat.lines.append('<' + chat.nick + '> ' + message)
            elif action == 'Disconnect' and chat:
                with LOCK:
                    chat.close()
                    SESSIONS.pop(key, None)
                cookie = ''
            else:
                raise ValueError('Session expired. Return to chat and join again.')
            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            if cookie is not None:
                self.send_header('Set-Cookie', 'retro_irc=' + cookie + '; Path=/; HttpOnly; SameSite=Strict')
            self.end_headers()
        except (ValueError, OSError):
            self.reply('<P>Unable to complete that action. Check the nickname/message, wait a moment, '
                       'or reconnect if IRC is unavailable or busy.</P><P><A HREF="/">Return to chat</A></P>', 400)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    threading.Thread(target=janitor, daemon=True).start()
    ThreadingHTTPServer((HOST, 8088), Handler).serve_forever()
