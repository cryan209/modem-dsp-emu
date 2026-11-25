#!/usr/bin/python3
"""Private, on-demand legacy radio transcoder. No media archive."""
from collections import Counter
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
import re
import selectors
import subprocess
import threading
from urllib.parse import urlsplit

HOST = '10.150.150.157'
PORT = 8089
BASE = 'http://%s:%s' % (HOST, PORT)
SOURCE = 'https://station.thecheese.co.nz/listen/the_cheese/128.mp3'
RATES = (8, 16, 24, 32, 40, 48, 56, 64)
PRIVATE = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))
LOCK = threading.Lock()
LISTENERS = Counter()
LIMIT = 8


def profile(path):
    match = re.fullmatch(r'/cheese/(8|16|24|32|40|48|56|64)\.(mp3|wma|rm)', path)
    if not match:
        raise ValueError('Unknown stream')
    rate, kind = int(match[1]), match[2]
    if (kind == 'wma' and rate < 24) or (kind == 'rm' and rate != 8):
        raise ValueError('Unsupported codec bitrate')
    return rate, kind


def encoding(rate, kind):
    sample = 8000 if kind == 'rm' else (11025 if rate == 8 else 22050)
    codec, mux = {'mp3': ('libmp3lame', 'mp3'), 'wma': ('wmav2', 'asf'), 'rm': ('real_144', 'rm')}[kind]
    args = ['-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1', '-ac', '1', '-ar', str(sample),
            '-threads', '1', '-c:a', codec, '-b:a', str(rate * 1000)]
    if kind == 'mp3':
        args += ['-write_xing', '0', '-id3v2_version', '0']
    if kind == 'wma':
        args += ['-packet_size', '512']
    return args + ['-flush_packets', '1', '-f', mux, 'pipe:1']


def command(rate, kind):
    return ['/usr/bin/ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-tls_verify', '1', '-ca_file', '/etc/ssl/certs/ca-certificates.crt',
            '-threads', '1', '-filter_threads', '1', '-rw_timeout', '15000000', '-reconnect', '1', '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5', '-re', '-i', SOURCE] + encoding(rate, kind)


def index():
    rows = []
    for rate in RATES:
        cells = []
        for kind, playlist in (('mp3', 'm3u'), ('wma', 'asx'), ('rm', 'ram')):
            try:
                profile('/cheese/%s.%s' % (rate, kind))
            except ValueError:
                cells.append('<TD>--</TD>')
                continue
            path = '/cheese/%s.%s' % (rate, kind)
            cells.append('<TD><A HREF="%s.%s">Listen</A> | <A HREF="%s">Direct %s</A></TD>' % (path, playlist, path, kind.upper()))
        rows.append('<TR><TD>%s kbit/s</TD>%s</TR>' % (rate, ''.join(cells)))
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN"><HTML><HEAD><TITLE>Xtra Radio - The Cheese</TITLE></HEAD>'
            '<BODY BGCOLOR="#FFFFFF" TEXT="#000000"><H1>Xtra Radio</H1><H2>The Cheese</H2>'
            '<P>Music for Generation X. <A HREF="https://thecheese.co.nz/">Station website</A> | '
            '<A HREF="http://10.150.150.157:8086/">ISP services</A></P>'
            '<P>Live mono audio for dial-up. Try <B>16 kbit/s MP3</B> first. Listen links open an external player; '
            'use Direct or paste its URL into your player if file associations are missing.</P>'
            '<TABLE BORDER="1" CELLPADDING="6"><TR><TH>Audio rate</TH><TH>MP3</TH><TH>Windows Media</TH><TH>RealAudio</TH></TR>'
            + ''.join(rows) + '</TABLE>'
            '<P>Rates exclude container and network overhead. 8 kbit/s MP3 uses MPEG-2.5 and may need a newer MP3 player. '
            'WMA requires a WMA v2 capable player. RealAudio uses the old RealAudio 1.0 / 14.4 modem codec at 8 kbit/s, '
            'with speech-quality sound. Actual vintage-player compatibility is still being tested.</P>'
            '<P>WMA below 24 kbit/s and RealAudio above 8 kbit/s are unavailable with these encoders. '
            '48-64 kbit/s generally need multilink or LAN. Allow a few seconds to connect. '
            'If a stream stops, reopen it. Up to eight listeners, two per caller address.</P>'
            '<P>Conversion runs only while listening. No recordings are kept. '
            '<A HREF="' + SOURCE + '">Original 128 kbit/s MP3</A></P></BODY></HTML>').encode('ascii')



class RealAudioTiming:
    """Fix FFmpeg RM muxer's zero timestamps for 20-byte RA144 frames."""
    def __init__(self):
        self.buffer = bytearray()
        self.packets = False
        self.timestamp = 0

    def feed(self, data):
        self.buffer.extend(data)
        output = bytearray()
        while True:
            if not self.packets:
                if len(self.buffer) < 10:
                    break
                size = int.from_bytes(self.buffer[4:8], 'big')
                is_data = self.buffer[:4] == b'DATA'
                if is_data:
                    size = 18
                if not 10 <= size <= 65536:
                    raise ValueError('Invalid RM header')
                if len(self.buffer) < size:
                    break
                output.extend(self.buffer[:size])
                del self.buffer[:size]
                self.packets = is_data
            else:
                if len(self.buffer) < 12:
                    break
                size = int.from_bytes(self.buffer[2:4], 'big')
                if size < 32 or (size - 12) % 20:
                    raise ValueError('Invalid RA144 packet')
                if len(self.buffer) < size:
                    break
                packet = self.buffer[:size]
                packet[6:10] = self.timestamp.to_bytes(4, 'big')
                self.timestamp = (self.timestamp + size - 12) % (2**32)
                output.extend(packet)
                del self.buffer[:size]
        return bytes(output)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass

    def headers_for(self, code, kind, length=None):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        address = self.client_address[0]
        if not any(ipaddress.ip_address(address) in net for net in PRIVATE):
            self.send_error(403)
            return
        path = urlsplit(self.path).path
        if path in ('/', '/radio', '/radio/'):
            body = index()
            self.headers_for(200, 'text/html; charset=us-ascii', len(body))
            if not head:
                self.wfile.write(body)
            return
        playlist = None
        for suffix in ('.m3u', '.asx', '.ram'):
            if path.endswith(suffix):
                path, playlist = path[:-len(suffix)], suffix
                break
        try:
            rate, kind = profile(path)
        except ValueError:
            self.send_error(404)
            return
        if playlist:
            if playlist != {'mp3': '.m3u', 'wma': '.asx', 'rm': '.ram'}[kind]:
                self.send_error(404)
                return
            url = BASE + path
            mime = {'.m3u': 'audio/x-mpegurl', '.asx': 'video/x-ms-asf', '.ram': 'audio/x-pn-realaudio'}[playlist]
            if playlist == '.asx':
                body = ('<ASX VERSION="3.0"><ENTRY><TITLE>The Cheese - %s kbit/s</TITLE><REF HREF="%s" /></ENTRY></ASX>\r\n' % (rate, escape(url, quote=True))).encode('ascii')
            elif playlist == '.m3u':
                body = ('#EXTM3U\r\n#EXTINF:-1,The Cheese - %s kbit/s\r\n%s\r\n' % (rate, url)).encode('ascii')
            else:
                body = (url + '\r\n').encode('ascii')
            self.headers_for(200, mime, len(body))
            if not head:
                self.wfile.write(body)
            return
        mime = {'mp3': 'audio/mpeg', 'wma': 'audio/x-ms-wma', 'rm': 'application/vnd.rn-realmedia'}[kind]
        if head:
            self.headers_for(200, mime)
            return
        with LOCK:
            if sum(LISTENERS.values()) >= LIMIT or LISTENERS[address] >= 2:
                self.send_error(503, 'Radio is full; try again shortly')
                return
            LISTENERS[address] += 1
        process = None
        headers_sent = False
        timing = RealAudioTiming() if kind == 'rm' else None
        try:
            process = subprocess.Popen(command(rate, kind), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            # Detect failed source/encoder before sending a successful HTTP status.
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                if not ready.select(25):
                    self.send_error(502, 'Station unavailable; try again shortly')
                    return
                first = os.read(process.stdout.fileno(), 1024)
                if not first:
                    self.send_error(502, 'Station unavailable; try again shortly')
                    return
                self.connection.settimeout(20)
                self.headers_for(200, mime)
                headers_sent = True
                self.wfile.write(timing.feed(first) if timing else first)
                while ready.select(30):
                    data = os.read(process.stdout.fileno(), 1024)
                    if not data:
                        break
                    self.wfile.write(timing.feed(data) if timing else data)
        except (OSError, ValueError, subprocess.SubprocessError):
            if not headers_sent:
                try:
                    self.send_error(502, 'Station unavailable; try again shortly')
                except OSError:
                    pass
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
            with LOCK:
                LISTENERS[address] -= 1
                if not LISTENERS[address]:
                    del LISTENERS[address]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(10)
        return conn, address


if __name__ == '__main__':
    Server((HOST, PORT), Handler).serve_forever()
