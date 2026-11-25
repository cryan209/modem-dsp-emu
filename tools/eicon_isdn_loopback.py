#!/usr/bin/env python3
"""Exercise the host native ISDN adapters over a clocked clear B-channel.

No audio, SIP account, original firmware or physical ISDN line is involved.
Produces raw B-channel captures, independent known-payload comparisons and
PPP/CHAP/IPCP/ICMP results. A configuration match alone never counts as a pass.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random

from eicon_at import AtParser, PROFILES
from eicon_idi import IsdnOptions
from eicon_isdn_profile_probe import probe_profile, NATIVE_PROFILES
from isdn_bchannel import NativeBChannel, PppIsdnAdapter
from ppp import PppConfig, PppPeer, icmp_echo_request


def make_pair(profile, user_rate):
    at = AtParser()
    command = f'AT&F{profile}'
    if profile in (2, 3):
        command += '+IB' + str({9600: 5, 19200: 6, 38400: 7}[user_rate])
    at.feed((command + '\r').encode())
    selected = at.isdn_options()
    options = IsdnOptions(selected.profile, user_rate=user_rate)
    return (NativeBChannel(options, calling=True),
            NativeBChannel(options, calling=False))


def exchange(pair, now, captures=None):
    a, b = pair
    a.tick(now)
    b.tick(now)
    aw, bw = a.take_wire(160), b.take_wire(160)
    if captures:
        captures[0].write(aw)
        captures[1].write(bw)
    b.feed_wire(aw)
    a.feed_wire(bw)


def received(endpoint):
    return endpoint.take_data() + b''.join(endpoint.take_frames())


def queue_payload(channel, data):
    if channel.profile in (9, 10):
        for offset in range(0, len(data), channel.options.max_frame):
            channel.queue_data(data[offset:offset + channel.options.max_frame])
    else:
        channel.queue_data(data)


def test_payload(profile, user_rate=38400, size=16384, seconds=10, captures=None):
    rng = random.Random(0xE1C000 + profile)
    expected = (bytes(range(256)) + rng.randbytes(size))[0:size]
    reverse = expected[::-1]
    pair = make_pair(profile, user_rate)
    queue_payload(pair[0], expected)
    queue_payload(pair[1], reverse)
    rx = (bytearray(), bytearray())
    elapsed = 0
    for tick in range(round(seconds * 50)):
        elapsed = (tick + 1) / 50
        exchange(pair, tick / 50, captures)
        for endpoint, output in zip(pair, rx):
            output.extend(received(endpoint))
        if len(rx[0]) >= size and len(rx[1]) >= size:
            break
    actual = (bytes(rx[0][:size]), bytes(rx[1][:size]))
    result = {'bytes_each_direction': size, 'seconds': elapsed,
              'answer_to_caller_matches': actual[0] == reverse,
              'caller_to_answer_matches': actual[1] == expected,
              'expected_sha256': hashlib.sha256(expected).hexdigest(),
              'caller_to_answer_sha256': hashlib.sha256(actual[1]).hexdigest(),
              'answer_to_caller_sha256': hashlib.sha256(actual[0]).hexdigest(),
              'bad_fcs': [e.hdlc.bad_fcs for e in pair],
              'wire_octets_each_direction': pair[0].tx_octets,
              'compressed': [bool(e.link and e.link.compression) for e in pair]}
    return result


def test_ppp(profile, user_rate=38400, count=20, seconds=10, captures=None):
    pair = make_pair(profile, user_rate)
    config = {'synchronous': profile in (9, 10), 'echo_interval': 0}
    client = PppPeer(PppConfig(role='client', local_address='100.64.0.2',
                               peer_address='100.64.0.1', **config), log=lambda _: None)
    server = PppPeer(PppConfig(role='server', **config), log=lambda _: None)
    adapters = [PppIsdnAdapter(pair[0], client), PppIsdnAdapter(pair[1], server)]
    sent, sequences = 0, set()
    up_at = None
    for tick in range(round(seconds * 50)):
        now = tick / 50
        for adapter in adapters:
            adapter.transmit(now)
        exchange(pair, now, captures)
        for adapter in adapters:
            adapter.receive(now)
        if client.up and server.up:
            if up_at is None:
                up_at = now
            if sent < count and tick % 5 == 0:
                sent += 1
                client.send_ip(icmp_echo_request('100.64.0.2', '100.64.0.1', sequence=sent))
        for packet in client.rx_ip:
            if len(packet) >= 28 and packet[20:22] == b'\x00\x00':
                sequences.add(int.from_bytes(packet[26:28], 'big'))
        client.rx_ip.clear()
    return {'client_ipcp_up': client.up, 'server_ipcp_up': server.up,
            'authenticated': server.authenticated, 'up_at_seconds': up_at,
            'duration_seconds': seconds, 'pings_sent': sent,
            'ping_reply_sequences': sorted(sequences),
            'pings_received': len(sequences),
            'ppp_fcs_errors': [client.framer.fcs_errors, server.framer.fcs_errors],
            'bchannel_bad_fcs': [e.hdlc.bad_fcs for e in pair],
            'link_failures': [e.link.failed if e.link else None for e in pair],
            'link_invalid_frames': [e.link.invalid if e.link else 0 for e in pair],
            'retransmitted_iframes': [e.link.retransmits if e.link else 0 for e in pair],
            'wire_octets_each_direction': pair[0].tx_octets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=int, action='append', choices=NATIVE_PROFILES)
    parser.add_argument('--user-rate', type=int, choices=(9600, 19200, 38400), default=38400)
    parser.add_argument('--payload-bytes', type=int, default=16384)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--ping-count', type=int, default=20)
    parser.add_argument('--capture-dir', type=Path, default=Path('artifacts/isdn-loopback'))
    args = parser.parse_args()
    if not 1 <= args.payload_bytes <= 65536 or args.seconds <= 0 or args.ping_count < 1:
        parser.error('payload must be 1..65536 bytes; duration and ping count must be positive')
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for profile in args.profile or NATIVE_PROFILES:
        captures = [open(args.capture_dir / f'profile-{profile}.{phase}.{side}.bchan', 'wb')
                    for phase in ('payload', 'ppp') for side in ('caller', 'answerer')]
        try:
            setup = probe_profile(profile, args.user_rate)
            payload = test_payload(profile, args.user_rate, args.payload_bytes,
                                   args.seconds, captures[:2])
            ppp = test_ppp(profile, args.user_rate, args.ping_count, args.seconds, captures[2:])
        finally:
            for capture in captures:
                capture.close()
        passed = (setup['configuration_matches'] and payload['answer_to_caller_matches']
                  and payload['caller_to_answer_matches'] and ppp['client_ipcp_up']
                  and ppp['server_ipcp_up'] and ppp['authenticated']
                  and ppp['ping_reply_sequences'] == list(range(1, args.ping_count + 1))
                  and not any(ppp['ppp_fcs_errors'] + ppp['bchannel_bad_fcs'])
                  and not any(ppp['link_failures']))
        result = {'profile': profile, 'name': PROFILES[profile][0],
                  'backend': 'host-native-bchannel', 'firmware_executed': False,
                  'physical_line_tested': False, 'passed': passed,
                  'user_rate': args.user_rate, 'setup': setup,
                  'payload': payload, 'ppp': ppp}
        results.append(result)
        print(f"{profile:2} {result['name']}: {'PASS' if passed else 'FAIL'}; "
              f"payload {args.payload_bytes} bytes each way; "
              f"PPP {ppp['pings_received']}/{ppp['pings_sent']} replies")
    (args.capture_dir / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'Results: {args.capture_dir / "results.json"} (host backend; no hardware claim)')
    return int(not all(r['passed'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
