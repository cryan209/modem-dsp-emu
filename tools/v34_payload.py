"""Opt-in, exact bidirectional UDP payload probe carried by PPP (stdlib only).

Stop-and-wait, not a throughput benchmark. Each response contains independently
chosen downstream bytes, not an echo. A per-call nonce prevents stale packets
from completing a later call. Keep IPv4+UDP+probe below one LAPM N401=128 frame.
"""
from __future__ import annotations

import hashlib
import json
import socket
import struct
import threading
import time
from pathlib import Path

MAGIC = b'V34Q'
HEADER = struct.Struct('!4s16sI')
BLOCK = 64
MAX_BYTES = 16 * 1024 * 1024


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def payload(token: bytes, direction: bytes, sequence: int, size: int) -> bytes:
    length = min(BLOCK, size - sequence * BLOCK)
    if len(token) != 16 or direction not in (b'up', b'down') or not 0 < length <= BLOCK:
        raise ValueError('invalid payload coordinates')
    return hashlib.shake_256(token + direction + struct.pack('!I', sequence)).digest(length)


def expected_hash(token: bytes, direction: bytes, size: int) -> str:
    digest = hashlib.sha256()
    for sequence in range((size + BLOCK - 1) // BLOCK):
        digest.update(payload(token, direction, sequence, size))
    return digest.hexdigest()


class Receiver:
    """Host-side verifier, reached only through answerer PPP usernet/NAT."""

    def __init__(self, token: bytes, size: int):
        if len(token) != 16 or not 1 <= size <= MAX_BYTES:
            raise ValueError('invalid token or byte count')
        self.token, self.size = token, size
        self.blocks = (size + BLOCK - 1) // BLOCK
        self.received: dict[int, bytes] = {}
        self.errors: list[str] = []
        self.duplicates = 0

    def accept(self, packet: bytes) -> bytes | None:
        if len(packet) < HEADER.size:
            self.errors.append('truncated request')
            return None
        magic, token, sequence = HEADER.unpack_from(packet)
        if magic != MAGIC or token != self.token or sequence >= self.blocks:
            self.errors.append('wrong session or sequence')
            return None
        data = packet[HEADER.size:]
        if data != payload(token, b'up', sequence, self.size):
            self.errors.append(f'upstream mismatch at block {sequence}')
            return None
        if sequence in self.received:
            self.duplicates += 1
        self.received[sequence] = data
        return HEADER.pack(MAGIC, token, sequence) + payload(token, b'down', sequence, self.size)

    def result(self) -> dict:
        digest = hashlib.sha256()
        for sequence in sorted(self.received):
            digest.update(self.received[sequence])
        return dict(complete=len(self.received) == self.blocks and not self.errors,
                    bytes=sum(map(len, self.received.values())),
                    blocks=len(self.received), sha256=digest.hexdigest(),
                    duplicates=self.duplicates, errors=self.errors.copy())


class Server:
    """Bounded localhost UDP service; close before inspecting its receiver."""

    def __init__(self, receiver: Receiver):
        self.receiver = receiver
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', 0))
        self.socket.settimeout(0.1)
        self.port = self.socket.getsockname()[1]
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def _run(self):
        while not self.stopping.is_set():
            try:
                packet, peer = self.socket.recvfrom(65535)
                response = self.receiver.accept(packet)
                if response is not None:
                    self.socket.sendto(response, peer)
            except socket.timeout:
                continue
            except OSError as exc:
                self.receiver.errors.append(f'UDP server: {exc}')
                return

    def __exit__(self, *_):
        self.stopping.set()
        self.thread.join(timeout=2)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError('UDP server did not stop')


class Client:
    RETRY_SECONDS = 2.0

    def __init__(self, config: Path):
        value = json.loads(config.read_text())
        self.token = bytes.fromhex(value['token'])
        self.size = value['bytes']
        self.port = value['port']
        if (len(self.token) != 16 or type(self.size) is not int
                or not 1 <= self.size <= MAX_BYTES or type(self.port) is not int
                or not 1 <= self.port <= 65535):
            raise ValueError('invalid qualification configuration')
        self.report = Path(value['report'])
        self.blocks = (self.size + BLOCK - 1) // BLOCK
        self.sequence = 0
        self.started_at = self.finished_at = None
        self.sent_at = None
        self.retries = 0
        self.errors: list[str] = []
        self.digest = hashlib.sha256()
        self.source_port = 47899

    @property
    def finished(self):
        return self.sequence == self.blocks

    def result(self):
        return dict(token=self.token.hex(), complete=self.finished and not self.errors,
                    bytes=min(self.sequence * BLOCK, self.size), blocks=self.sequence,
                    sha256=self.digest.hexdigest(), retries=self.retries,
                    transfer_seconds=(self.finished_at - self.started_at
                                      if self.finished_at is not None else None),
                    errors=self.errors.copy())

    def close(self):
        write_json(self.report, self.result())

    def pump(self, peer, now=None):
        from usernet import build_ipv4, build_udp, parse_ipv4
        if not peer.up or self.finished or self.errors:
            return
        now = time.monotonic() if now is None else now
        if self.started_at is None:
            self.started_at = now
        keep = []
        for packet in peer.rx_ip:
            parsed = parse_ipv4(packet)
            if parsed is None or parsed[2] != 17 or len(parsed[3]) < 8:
                keep.append(packet)
                continue
            sport, dport, length, _checksum = struct.unpack('!HHHH', parsed[3][:8])
            if dport != self.source_port:
                keep.append(packet)
                continue
            body = parsed[3][8:]
            if (parsed[0] != socket.inet_aton('127.0.0.1') or sport != self.port
                    or length != len(parsed[3]) or len(body) < HEADER.size):
                self.errors.append('malformed downstream packet')
                continue
            magic, token, sequence = HEADER.unpack_from(body)
            if (magic != MAGIC or token != self.token
                    or sequence >= self.blocks or sequence > self.sequence):
                self.errors.append('wrong downstream session or sequence')
                continue
            if body[HEADER.size:] != payload(token, b'down', sequence, self.size):
                self.errors.append(f'downstream mismatch at block {sequence}')
                continue
            if sequence == self.sequence:
                self.digest.update(body[HEADER.size:])
                self.sequence += 1
                self.sent_at = None
        peer.rx_ip[:] = keep
        if self.finished:
            self.finished_at = now
            self.close()
            if not self.errors:
                print(f'[qualify] exact downstream payload verified: {self.size} bytes', flush=True)
            return
        if self.errors:
            self.close()
            return
        if self.sent_at is None or now - self.sent_at >= self.RETRY_SECONDS:
            if self.sent_at is not None:
                self.retries += 1
            source = socket.inet_aton(peer.ipcp.local_address)
            host = socket.inet_aton('127.0.0.1')
            body = HEADER.pack(MAGIC, self.token, self.sequence) + payload(
                self.token, b'up', self.sequence, self.size)
            peer.send_ip(build_ipv4(source, host, 17, build_udp(
                source, host, self.source_port, self.port, body)))
            self.sent_at = now
