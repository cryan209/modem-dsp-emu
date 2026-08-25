#!/usr/bin/env python3
"""Receive the live PPP file-transfer probe's acknowledged UDP blocks."""
from __future__ import annotations

import argparse
import hashlib
import socket
import struct
from pathlib import Path

MAGIC = b'E5MB'
HEADER = struct.Struct('!4sIIII')
ACK = struct.Struct('!4sII')
BLOCK = 1400


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--bind', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=47900)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    received: set[int] = set()
    total = None
    size = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((args.bind, args.port))
        print(f'[file-sink] listening on {args.bind}:{args.port}', flush=True)
        with args.output.open('w+b') as output:
            while total is None or len(received) < total:
                packet, address = sock.recvfrom(2048)
                if len(packet) < HEADER.size:
                    continue
                magic, transfer, sequence, packet_total, length = \
                    HEADER.unpack(packet[:HEADER.size])
                data = packet[HEADER.size:]
                if magic != MAGIC or packet_total == 0 or length != len(data):
                    continue
                if total is None:
                    total = packet_total
                    print(f'[file-sink] transfer={transfer} blocks={total}',
                          flush=True)
                if packet_total != total or sequence >= total:
                    continue
                if sequence not in received:
                    output.seek(sequence * BLOCK)
                    output.write(data)
                    size += len(data)
                    received.add(sequence)
                sock.sendto(ACK.pack(MAGIC, transfer, sequence), address)
                if len(received) % 256 == 0 or len(received) == total:
                    print(f'[file-sink] blocks={len(received)}/{total} '
                          f'bytes={size}', flush=True)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f'[file-sink] complete bytes={args.output.stat().st_size} '
          f'sha256={digest}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
