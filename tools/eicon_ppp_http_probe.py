#!/usr/bin/env python3
"""Temporary HTTP peer for exact modem PPP download/upload verification."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bind', required=True)
    ap.add_argument('--port', type=int, default=47902)
    ap.add_argument('--bytes', type=int, default=262144)
    args = ap.parse_args()
    download = hashlib.shake_256(b'emu-ppp-download-v1').digest(args.bytes)
    upload = hashlib.shake_256(b'emu-ppp-upload-v1').digest(args.bytes)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(download)))
            self.end_headers()
            self.wfile.write(download)
            print(json.dumps(dict(event='download_sent', bytes=len(download),
                                  sha256=hashlib.sha256(download).hexdigest())), flush=True)

        def do_POST(self):
            self.connection.settimeout(180)
            count = int(self.headers.get('Content-Length', '-1'))
            payload = self.rfile.read(count) if count == len(upload) else b''
            passed = payload == upload
            receipt = dict(event='upload_received', passed=passed, bytes=len(payload),
                           sha256=hashlib.sha256(payload).hexdigest())
            body = json.dumps(receipt).encode()
            self.send_response(200 if passed else 400)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            print(json.dumps(receipt), flush=True)

    with HTTPServer((args.bind, args.port), Handler) as server:
        print(json.dumps(dict(event='ready', bind=args.bind, port=args.port)), flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
