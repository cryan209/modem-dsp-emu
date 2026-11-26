"""Fail-closed source-side timing checks for the qualifier's RAW IPv4 PCAPs.

This intentionally accepts only the capture format emitted by RtpCapture,
not arbitrary Wireshark input. Preserve record order; never sort away reorders
or host wall-clock discontinuities. Memory is bounded per SSRC, not per packet.
"""
from dataclasses import dataclass, field
import math
from pathlib import Path
import socket
import struct


@dataclass
class Stream:
    source: str
    sport: int
    destination: str
    dport: int
    ssrc: int
    payload_type: int
    packets: int = 0
    payload_sizes: set = field(default_factory=set)
    sequence_gaps: int = 0
    timestamp_jumps: int = 0
    nonincreasing_wall_times: int = 0
    timestamp_span: int = 0
    first_wall: int = 0
    last_wall: int = 0
    last_seq: int = 0
    last_timestamp: int = 0
    last_payload_size: int = 0
    min_gap_ns: int | None = None
    max_gap_ns: int | None = None

    def add(self, wall, seq, timestamp, payload_size):
        if self.packets:
            self.sequence_gaps += ((seq - self.last_seq) & 0xffff) != 1
            delta = (timestamp - self.last_timestamp) & 0xffffffff
            self.timestamp_jumps += delta != self.last_payload_size
            self.timestamp_span += delta
            gap = wall - self.last_wall
            self.nonincreasing_wall_times += gap <= 0
            self.min_gap_ns = gap if self.min_gap_ns is None else min(gap, self.min_gap_ns)
            self.max_gap_ns = gap if self.max_gap_ns is None else max(gap, self.max_gap_ns)
        else:
            self.first_wall = wall
        self.last_wall, self.last_seq, self.last_timestamp = wall, seq, timestamp
        self.last_payload_size = payload_size
        self.payload_sizes.add(payload_size)
        self.packets += 1

    def verdict(self, expected_packets, max_clock_ppm, max_gap_ms):
        duration = (self.last_wall - self.first_wall) / 1e9
        rate = self.timestamp_span / duration if duration > 0 else None
        ppm = (rate / 8000 - 1) * 1e6 if rate is not None else None
        checks = dict(complete_capture=type(expected_packets) is int and self.packets == expected_packets,
                      measurable_span=self.packets >= 2 and duration > 0,
                      payload_size=self.payload_sizes == {160},
                      sequence_continuity=self.sequence_gaps == 0,
                      timestamp_continuity=self.timestamp_jumps == 0,
                      increasing_capture_clock=self.nonincreasing_wall_times == 0,
                      clock_budget=ppm is not None and math.isfinite(ppm) and abs(ppm) <= max_clock_ppm)
        maximum_gap = self.max_gap_ns / 1e6 if self.max_gap_ns is not None else None
        if max_gap_ms is not None:
            checks['gap_budget'] = maximum_gap is not None and maximum_gap <= max_gap_ms
        return dict(passed=all(checks.values()), checks=checks, packets=self.packets,
                    ssrc=self.ssrc, payload_type=self.payload_type,
                    source=self.source, source_port=self.sport,
                    destination=self.destination, destination_port=self.dport,
                    wall_seconds=duration, timestamp_hz=rate, clock_ppm=ppm,
                    sequence_gaps=self.sequence_gaps, timestamp_jumps=self.timestamp_jumps,
                    nonincreasing_wall_times=self.nonincreasing_wall_times,
                    min_gap_ms=self.min_gap_ns / 1e6 if self.min_gap_ns is not None else None,
                    max_gap_ms=maximum_gap)


def read_capture(path: Path) -> list[Stream]:
    streams = {}
    with path.open('rb') as handle:
        header = handle.read(24)
        formats = {b'\xd4\xc3\xb2\xa1': ('<', 1000, 1_000_000),
                   b'\xa1\xb2\xc3\xd4': ('>', 1000, 1_000_000),
                   b'\x4d\x3c\xb2\xa1': ('<', 1, 1_000_000_000),
                   b'\xa1\xb2\x3c\x4d': ('>', 1, 1_000_000_000)}
        if len(header) != 24 or header[:4] not in formats:
            raise ValueError('missing or invalid PCAP header')
        endian, scale, fractions = formats[header[:4]]
        _, major, minor, _, _, snaplen, link = struct.unpack(endian + 'IHHIIII', header)
        if (major, minor, link) != (2, 4, 101) or not 40 <= snaplen <= 65535:
            raise ValueError('expected complete RAW IPv4 PCAP v2.4')
        while record := handle.read(16):
            if len(record) != 16:
                raise ValueError('truncated PCAP record header')
            seconds, fraction, size, original = struct.unpack(endian + 'IIII', record)
            if fraction >= fractions or size != original or not 40 <= size <= snaplen:
                raise ValueError('invalid or snaplen-truncated PCAP record')
            packet = handle.read(size)
            if len(packet) != size:
                raise ValueError('truncated PCAP packet')
            ihl = (packet[0] & 15) * 4
            total, fragment = struct.unpack('!HH', packet[2:4] + packet[6:8])
            if (packet[0] >> 4 != 4 or ihl < 20 or len(packet) < ihl + 20
                    or total != size or fragment & 0x3fff or packet[9] != 17):
                raise ValueError('expected unfragmented IPv4 UDP RTP')
            sport, dport, length, _ = struct.unpack('!HHHH', packet[ihl:ihl + 8])
            rtp = packet[ihl + 8:]
            if length != size - ihl or len(rtp) < 12 or rtp[0] != 0x80:
                raise ValueError('invalid UDP/RTP lengths or unsupported RTP header')
            seq, timestamp, ssrc = struct.unpack('!HII', rtp[2:12])
            key = (socket.inet_ntoa(packet[12:16]), sport,
                   socket.inet_ntoa(packet[16:20]), dport, ssrc, rtp[1] & 127)
            if key not in streams:
                if len(streams) >= 200:  # two streams per maximum 100-call run
                    raise ValueError('too many RTP streams')
                streams[key] = Stream(*key)
            streams[key].add(seconds * 1_000_000_000 + fraction * scale,
                             seq, timestamp, len(rtp) - 12)
    return list(streams.values())


def is_flow(stream, source_port, destination_port):
    return (stream.source == stream.destination == '127.0.0.1'
            and stream.sport == source_port and stream.dport == destination_port
            and stream.payload_type == 0)


def evaluate_pair(caller_capture, answerer_capture, caller_port, answerer_port,
                  caller_packets, answerer_packets, max_clock_ppm=1000, max_gap_ms=None):
    caller = [s for s in caller_capture if s.sport == caller_port]
    peer = [s for s in caller_capture if s.sport == answerer_port]
    result = dict(passed=False, checks=dict(caller_identity=False, answerer_identity=False))
    if len(caller) != 1 or not is_flow(caller[0], caller_port, answerer_port):
        return result
    result['checks']['caller_identity'] = True
    result['caller'] = caller[0].verdict(caller_packets, max_clock_ppm, max_gap_ms)
    # The caller's incoming stream supplies identity ONLY, never the answerer's
    # timing. Match its SSRC to the answering endpoint's own TX capture.
    if len(peer) != 1 or not is_flow(peer[0], answerer_port, caller_port):
        return result
    answerer = [s for s in answerer_capture if is_flow(s, answerer_port, caller_port)
                and s.ssrc == peer[0].ssrc]
    if len(answerer) != 1:
        return result
    result['checks']['answerer_identity'] = True
    result['answerer'] = answerer[0].verdict(answerer_packets, max_clock_ppm, max_gap_ms)
    result['passed'] = (all(result['checks'].values()) and result['caller']['passed']
                        and result['answerer']['passed'])
    return result
