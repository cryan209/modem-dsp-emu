#!/usr/bin/env python3
"""Original MIPS/HSCX firmware against an independent host ISDN peer.

The DSP generates and receives the clear wire. The host peer independently
checks framing/FCS. No audio converter, physical card, or external line is used.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import random

import eicon_mips_shim as fw
from eicon_idi import IsdnOptions
from eicon_isdn_firmware_bchannel import FirmwareBChannel
from eicon_isdn_loopback import exchange, queue_payload, received
from isdn_bchannel import NativeBChannel, PppIsdnAdapter
from ppp import PppConfig, PppPeer, icmp_echo_request


def exercise(shim, args):
    options = IsdnOptions(args.profile, max_frame=args.frame_bytes)
    card = FirmwareBChannel(shim, options)
    peer = NativeBChannel(options, calling=False)
    pair = card, peer
    expected = (bytes(range(256)) + random.Random(90).randbytes(args.payload_bytes))[:args.payload_bytes]
    rx = bytearray(), bytearray()
    captures = [open(args.capture_dir / f'{name}.bchan', 'wb') for name in ('firmware', 'peer')]
    try:
        # Clock the task initializer and establishment handshake before
        # sending DTE bytes. A transparent bearer has no frame to recover
        # bytes sent while its receiver was still starting.
        for tick in range(20):
            exchange(pair, tick / 50, captures)
            received(card)
            received(peer)
        queue_payload(card, expected)
        queue_payload(peer, expected[::-1])
        now = .4
        for tick in range(500):
            now = .4 + tick / 50
            exchange(pair, now, captures)
            for channel, output in zip(pair, rx):
                output.extend(received(channel))
            if (args.profile == 8 and expected[::-1] in rx[0] and expected in rx[1]) or (
                    args.profile != 8 and len(rx[0]) >= len(expected) and len(rx[1]) >= len(expected)):
                break
        if args.profile == 8:
            # Idle octets are part of a transparent stream; compare the
            # complete known sequence at its observed stream offset.
            for output, wanted in zip(rx, (expected[::-1], expected)):
                offset = output.find(wanted)
                if offset >= 0:
                    output[:] = output[offset:offset + len(wanted)]
        payload_result = {'bytes_each_direction': len(expected),
                          'firmware_to_peer_matches': bytes(rx[1]) == expected,
                          'peer_to_firmware_matches': bytes(rx[0]) == expected[::-1],
                          'firmware_to_peer_sha256': hashlib.sha256(rx[1]).hexdigest(),
                          'peer_to_firmware_sha256': hashlib.sha256(rx[0]).hexdigest(),
                          'expected_sha256': hashlib.sha256(expected).hexdigest(),
                          'seconds': now + .02}
        ppp = None
        if args.profile in (1, 2, 3, 6, 7, 8, 9, 10, 15):
            common = dict(synchronous=args.profile in (9, 10), echo_interval=0)
            client = PppPeer(PppConfig(role='client', local_address='100.64.0.2',
                                      peer_address='100.64.0.1', **common), log=lambda _: None)
            server = PppPeer(PppConfig(role='server', **common), log=lambda _: None)
            adapters = PppIsdnAdapter(card, client), PppIsdnAdapter(peer, server)
            sent, replies, up_at = 0, set(), None
            for tick in range(round(args.seconds * 50)):
                stamp = now + .02 + tick / 50
                for adapter in adapters:
                    adapter.transmit(stamp)
                exchange(pair, stamp, captures)
                for adapter in adapters:
                    adapter.receive(stamp)
                if client.up and server.up:
                    if up_at is None:
                        up_at = tick / 50
                    if sent < args.ping_count and tick % 5 == 0:
                        sent += 1
                        client.send_ip(icmp_echo_request('100.64.0.2', '100.64.0.1', sequence=sent))
                for packet in client.rx_ip:
                    if len(packet) >= 28 and packet[20:22] == b'\x00\x00':
                        replies.add(int.from_bytes(packet[26:28], 'big'))
                client.rx_ip.clear()
            ppp = {'client_up': client.up, 'server_up': server.up,
                   'authenticated': server.authenticated, 'up_at_seconds': up_at,
                   'duration_seconds': args.seconds, 'pings_sent': sent,
                   'reply_sequences': sorted(replies),
                   'ppp_fcs_errors': [client.framer.fcs_errors, server.framer.fcs_errors]}
    finally:
        for capture in captures:
            capture.close()
    result = {'profile': args.profile, 'firmware_executed': True,
              'physical_line': False, 'peer_backend': 'independent-host-native-bchannel',
              'payload': payload_result, 'ppp': ppp, 'bearer_connected': card.connected,
              'firmware_disconnects': card.disconnects, 'n_data_rejected': card.rejected,
              'peer_bad_fcs': peer.hdlc.bad_fcs, 'wire_octets': card.tx_octets,
              'unwritten_sport_intervals': card.unwritten}
    result['passed'] = (all(payload_result[k] for k in ('firmware_to_peer_matches', 'peer_to_firmware_matches'))
                        and card.connected and not card.disconnects and not card.rejected
                        and not peer.hdlc.bad_fcs and (ppp is None or
                        (ppp['client_up'] and ppp['server_up'] and ppp['authenticated']
                         and ppp['reply_sequences'] == list(range(1, args.ping_count + 1))
                         and not any(ppp['ppp_fcs_errors']))))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=int, choices=(1, 2, 3, 6, 7, 8, 9, 10, 15), default=9)
    parser.add_argument('--seconds', type=float, default=40)
    parser.add_argument('--ping-count', type=int, default=100)
    parser.add_argument('--payload-bytes', type=int, default=4096)
    parser.add_argument('--frame-bytes', type=int, default=2048)
    parser.add_argument('--capture-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0 or args.ping_count < 1 or not 1 <= args.payload_bytes <= 65536:
        parser.error('positive duration/ping count and 1..65536 payload bytes required')
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    original, saved_argv, result = fw.run_mainloop, sys.argv, {}
    def run(shim, config):
        original(shim, config)
        result.update(exercise(shim, args))
    fw.run_mainloop = run
    sys.argv = ['mips', '--kernel', 'artifacts/eicon-dsp/build-117-926/0009-diva-server-pri-30m-kernel',
                '--tikrnl', 'artifacts/eicon-dsp/build-117-926/0258-tikrnl81.f34-task',
                '--mainloop', '--isdn-profile', str(args.profile),
                '--isdn-max-frame', str(args.frame_bytes),
                '--simulate-outgoing-call', '--call-steps', '128']
    try:
        fw.main()
    finally:
        fw.run_mainloop, sys.argv = original, saved_argv
    (args.capture_dir / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print('[firmware-wire] ' + json.dumps(result, sort_keys=True))
    return int(not result['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
