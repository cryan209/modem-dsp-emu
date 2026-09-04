"""Deterministic frame-level bridge probe, with optional downstream sample slip."""
import argparse
import json
import os
import socket
import struct
import time
from pathlib import Path

from v42_lapm import LapmEndpoint
from v90_engine_frame_adapter import Phase3ProcessEngine


class PacketWire:
    """Packetize PCM over localhost UDP, then reassemble 160-sample DSP inputs."""

    def __init__(self, samples):
        self.samples = samples
        self.tx = bytearray()
        # Equal startup cushion for all packet sizes. No runtime padding.
        self.rx = bytearray([255] * 320)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(('127.0.0.1', 0))
        self.receiver.settimeout(2)
        self.seq = self.timestamp = self.packets = 0
        self.payload_sizes = set()

    def packet(self, payload):
        header = struct.pack('!BBHII', 0x80, 0, self.seq, self.timestamp, 1)
        self.sender.sendto(header + payload, self.receiver.getsockname())
        received, _ = self.receiver.recvfrom(65535)
        assert received == header + payload
        self.rx.extend(received[12:])
        self.payload_sizes.add(len(payload))
        self.seq = (self.seq + 1) & 65535
        self.timestamp = (self.timestamp + len(payload)) & 0xffffffff
        self.packets += 1

    def put(self, pcm):
        self.tx.extend(pcm)
        while len(self.tx) >= self.samples:
            payload = bytes(self.tx[:self.samples])
            del self.tx[:self.samples]
            self.packet(payload)

    def take(self):
        if len(self.rx) < 160:
            raise RuntimeError('packet reassembly underrun')
        pcm = bytes(self.rx[:160])
        del self.rx[:160]
        return pcm

    def close(self):
        self.sender.close()
        self.receiver.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seconds', type=float, default=90)
    ap.add_argument('--slip', type=int, default=0)
    ap.add_argument('--slip-at', type=float, default=40)
    ap.add_argument('--sideband', default='1')
    ap.add_argument('--packet-ms', type=int, choices=(10, 15, 20, 30))
    ap.add_argument('--insert-packet', action='store_true')
    ap.add_argument('--arm-samples', type=int, default=int(os.environ.get(
        'EICON_V90D_BRIDGE_EVENT_ARM_SAMPLES', '0')))
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.slip < 0:
        ap.error('--slip inserts samples and must be nonnegative')
    if args.insert_packet and not args.packet_ms:
        ap.error('--insert-packet requires --packet-ms')
    if args.packet_ms and args.slip:
        ap.error('use --insert-packet for packetized experiments')
    os.environ.update(EICON_V90A_DATA_SIDEBAND=args.sideband,
                      EICON_V90D_DATA_SIDEBAND=args.sideband,
                      EICON_V90D_BRIDGE_CP_LIVE='1',
                      EICON_V90D_BRIDGE_EVENT_ARM_SAMPLES=str(args.arm_samples),
                      EICON_REACTIVE_ENGINE_STDERR='inherit')
    frame = 0
    def logger(role):
        return lambda msg: print(f'{frame / 50:.2f}s {role}: {msg}', flush=True)
    a = LapmEndpoint(role='originator', log=logger('a'), detect=False,
                     retransmit_after=150, poll_after=50)
    d = LapmEndpoint(role='answerer', log=logger('d'), detect=False,
                     retransmit_after=150, poll_after=50)
    root = Path(__file__).resolve().parents[1] / 'artifacts/v90-reliability-investigation'
    ae = Phase3ProcessEngine(root / 'bridge-a', data_link=a)
    de = Phase3ProcessEngine(root / 'bridge-d', data_link=d)
    downstream = bytes([255]) * 160
    wire = bytearray()
    connected = []
    injected = False
    down_wire = PacketWire(args.packet_ms * 8) if args.packet_ms else None
    up_wire = PacketWire(args.packet_ms * 8) if args.packet_ms else None
    post_fault_bytes = None
    try:
        for frame in range(int(args.seconds * 50)):
            if down_wire is not None:
                if (args.insert_packet and not injected
                        and frame >= args.slip_at * 50):
                    down_wire.packet(bytes([255]) * down_wire.samples)
                    injected = True
                    post_fault_bytes = (len(a.rx_data), len(d.rx_data))
                    print(f'{frame/50:.2f}s inserted downstream RTP packet '
                          f'({down_wire.samples} samples)', flush=True)
                upstream = ae.exchange(down_wire.take())
                downstream = de.exchange(up_wire.take())
                up_wire.put(upstream)
                down_wire.put(downstream)
            else:
                wire.extend(downstream)
                if args.slip and not injected and frame >= args.slip_at * 50:
                    # Insert silence without restoring the downstream symbol grid.
                    wire[:0] = bytes([255]) * args.slip
                    injected = True
                    print(f'{frame/50:.2f}s injected {args.slip} samples', flush=True)
                rx = bytes(wire[:160]); del wire[:160]
                upstream = ae.exchange(rx)
                downstream = de.exchange(upstream)
            if a.connected and d.connected:
                if not connected:
                    connected.append(frame / 50)
                if frame % 50 == 0:
                    a.send(b'upstream-probe\n')
                    d.send(b'downstream-probe\n')
            # Let the asynchronous CP worker run; audio time remains explicit.
            time.sleep(.001)
        result = dict(seconds=args.seconds, slip=args.slip,
                      packet_ms=args.packet_ms, inserted_packet=args.insert_packet,
                      rtp_payload_sizes=sorted(down_wire.payload_sizes) if down_wire else [],
                      downstream_packets=down_wire.packets if down_wire else 0,
                      post_fault_rx_bytes=([len(a.rx_data)-post_fault_bytes[0],
                                            len(d.rx_data)-post_fault_bytes[1]]
                                           if post_fault_bytes else None),
                      arm_samples=args.arm_samples, sideband=args.sideband,
                      connected_at=connected,
                      a_connected=a.connected, d_connected=d.connected,
                      a_failed=a.failed, d_failed=d.failed,
                      a_good=a.decoder.good, a_bad=a.decoder.bad_fcs,
                      d_good=d.decoder.good, d_bad=d.decoder.bad_fcs,
                      a_generation=a.generation, d_generation=d.generation,
                      a_rx_bytes=len(a.rx_data), d_rx_bytes=len(d.rx_data),
                      a_payload_valid=bool(a.rx_data) and bytes(a.rx_data) == b'downstream-probe\n' *
                          (len(a.rx_data) // len(b'downstream-probe\n')),
                      d_payload_valid=bool(d.rx_data) and bytes(d.rx_data) == b'upstream-probe\n' *
                          (len(d.rx_data) // len(b'upstream-probe\n')))
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    finally:
        ae.close()
        de.close()
        if down_wire:
            down_wire.close()
            up_wire.close()


if __name__ == '__main__':
    main()
