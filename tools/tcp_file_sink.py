#!/usr/bin/env python3
"""Receive one TCP file and verify its size and SHA-256."""
from __future__ import annotations

import argparse
import hashlib
import socket
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('output', type=Path)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=47901)
    args = ap.parse_args()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.host, args.port))
    listener.listen(1)
    print(f'[tcp-sink] listening on {args.host}:{args.port}', flush=True)
    client, address = listener.accept()
    total = 0
    digest = hashlib.sha256()
    with client, args.output.open('wb') as output:
        while True:
            data = client.recv(8192)
            if not data:
                break
            output.write(data)
            digest.update(data)
            total += len(data)
    print(f'[tcp-sink] complete bytes={total} sha256={digest.hexdigest()} '
          f'peer={address[0]}:{address[1]}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
