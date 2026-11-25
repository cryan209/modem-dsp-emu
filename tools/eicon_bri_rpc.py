"""Local binary PCI bridge transport for the existing whole-board model.

All register operations use BriCard. ADVANCE executes the real MIPS/DSP
firmware; it does not manufacture signatures or IDI responses. The QEMU
frontend owns virtual time, PCI address decoding and delivery of INTA.
"""
from __future__ import annotations
import argparse
import gc
import json
import mmap
import os
from pathlib import Path
import socket
import struct
import sys
from eicon_bri_card import BriCard, MEMORY_SIZE
from eicon_bri_machine import BriMachine

MAGIC = 0x42524931
VERSION = 1
REQUEST = struct.Struct('<IBBHIQ')
REPLY = struct.Struct('<IIQII')
HELLO, READ, WRITE, ADVANCE, CONFIG_READ, CONFIG_WRITE, RESET = range(7)
MAX_ADVANCE = 1000000


class BriPciBackend:
    def __init__(self, card=None):
        self.card = card if card is not None else BriCard(reentrant=True)
        self.machine = None
        self.fault = None
        self.advanced = 0

    def reset(self):
        # Reset board controls/cores while preserving SDRAM, like the
        # driver's stop/reset sequence. A new process supplies power-on RAM.
        # Transport reset must recover from an interrupted PLX reset/reload.
        self.card.pci.write(0x44, 2, 0)
        self.card.bridge_control = 0
        self.card.local_mapping = True
        self.card.write(0, 0x69, 1, 0)
        self.card.write(3, 0x400, 4, 0)
        self.card.write(3, 0x200, 4, 0)
        self.card.write(3, 0, 4, 0)
        self.card.cpu_control = None
        self.machine = None
        self.fault = None
        self.advanced = 0

    def access(self, operation, bar, size, offset, value):
        if self.fault is not None and operation != RESET:
            raise RuntimeError(f'latched board fault: {self.fault}')
        if operation == HELLO:
            if (bar, size, offset, value) != (0, 0, 0, VERSION):
                raise ValueError('unsupported BRI bridge version')
            return VERSION
        if operation == RESET:
            if (bar, size, offset, value) != (0, 0, 0, 0):
                raise ValueError('malformed reset')
            self.reset()
        elif operation in (CONFIG_READ, CONFIG_WRITE):
            if bar != 0:
                raise ValueError('configuration BAR selector must be zero')
            if operation == CONFIG_READ:
                if value:
                    raise ValueError('read value must be zero')
                return self.card.pci.read(offset, size)
            if offset == 0x44 and value & 3 == 3 and self.card.cpu_running:
                raise NotImplementedError('D3hot transition with running firmware is unqualified')
            self.card.pci.write(offset, size, value)
        elif operation in (READ, WRITE):
            if self.card.pci.power_state:
                raise NotImplementedError('PCI BAR access in D3hot is unqualified')
            if operation == READ:
                if value:
                    raise ValueError('read value must be zero')
                return self.card.read(bar, offset, size)
            was_running = self.card.cpu_running
            self.card.write(bar, offset, size, value)
            if not was_running and self.card.cpu_running:
                # The guest has now staged its own firmware. Architectural
                # CP0 access hooks must be discovered in that loaded image.
                self.machine = BriMachine(self.card)
        elif operation == ADVANCE:
            if (bar, size, offset) != (0, 0, 0) or not 1 <= value <= MAX_ADVANCE:
                raise ValueError('invalid instruction advance')
            if self.card.cpu_running:
                if self.machine is None:
                    raise RuntimeError('running board has no processor')
                result = self.machine.run(value)
                self.advanced += value
                if result['fault'] is not None:
                    self.fault = result['fault']
                    raise RuntimeError(f'MIPS fault: {self.fault}')
        else:
            raise ValueError(f'unknown bridge operation {operation}')
        return 0

    def close(self):
        self.card.cpu_control = None
        self.machine = None
        self.card.close()

    @property
    def flags(self):
        return (int(bool(self.card.irq_asserted)) | (int(self.card.cpu_running) << 1)
                | (int(self.fault is not None) << 2)
                | (int(not self.card.local_mapping) << 3))


def receive_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            if data:
                raise ConnectionError('truncated BRI bridge request')
            return None
        data.extend(chunk)
    return bytes(data)


def serve_connection(connection, backend, trace=None):
    negotiated = False
    while True:
        packet = receive_exact(connection, REQUEST.size)
        if packet is None:
            return
        magic, operation, bar, size, offset, value = REQUEST.unpack(packet)
        status, result = 0, 0
        try:
            if magic != MAGIC or (not negotiated and operation != HELLO):
                raise ValueError('invalid BRI bridge header/handshake')
            result = backend.access(operation, bar, size, offset, value)
            negotiated = negotiated or operation == HELLO
        except (ValueError, RuntimeError, NotImplementedError) as error:
            status = 1
            backend.fault = str(error)
            print(json.dumps(dict(error=str(error), operation=operation, bar=bar,
                                  size=size, offset=offset, value=value)), file=sys.stderr, flush=True)
        if trace is not None:
            trace.write(json.dumps(dict(operation=operation, bar=bar, size=size,
                offset=offset, value=value, status=status, result=result,
                flags=backend.flags, instructions=backend.advanced)) + '\n')
            trace.flush()
        connection.sendall(REPLY.pack(MAGIC, status, result, backend.flags, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--summary', type=Path, help='write final firmware state before closing the board')
    parser.add_argument('--shared-ram', type=Path, help='create a shared SDRAM file for QEMU BAR2')
    parser.add_argument('--wire-pairs', action='store_true', help='connect explicit external D-channel and raw SPORT peers 0/1 and 2/3')
    parser.add_argument('--legacy', action='store_true', help='use legacy controller-window diagnostics')
    parser.add_argument('--stage-firmware', type=Path,
                        help='diagnostic only: stage reentrant firmware before the guest connects')
    args = parser.parse_args()
    backend = None
    mapping = None
    shared_created = False
    trace = None
    bound = False
    try:
        if args.shared_ram:
            fd = os.open(args.shared_ram, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            shared_created = True
            try:
                os.ftruncate(fd, MEMORY_SIZE)
                mapping = mmap.mmap(fd, MEMORY_SIZE)
            finally:
                os.close(fd)
        backend = BriPciBackend(BriCard(reentrant=not args.legacy, ram=mapping))
        if args.wire_pairs:
            for first, second in ((0, 1), (2, 3)):
                backend.card.ipacs[first].connect(backend.card.ipacs[second])
                for channel in (0, 1):
                    backend.card.connect_sport1(first * 2 + channel, second * 2 + channel, latency_words=2)
        if args.stage_firmware:
            backend.card.load_reentrant(args.stage_firmware / 'te_dmlt.2qm',
                                        args.stage_firmware / 'dspdload.bin')
        if args.trace:
            trace = args.trace.open('w')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(args.socket))
            bound = True
            listener.listen(1)
            print(f'BRI bridge listening: {args.socket}', flush=True)
            connection, _ = listener.accept()
            with connection:
                serve_connection(connection, backend, trace)
    finally:
        if backend:
            if args.summary:
                state = backend.card.snapshot()
                args.summary.write_text(json.dumps(dict(
                    controllers=state['controllers'], allocated_dsps=state['allocated_dsps'],
                    cpu_running=state['cpu_running'], instructions=backend.advanced,
                    fault=backend.fault, fpga=state['fpga'], sport_peers=state['sport_peers'],
                    power_state=backend.card.pci.power_state,
                    power_transitions=backend.card.pci.power_transitions,
                    sport_frames=state['sport_frames']), indent=2) + '\n')
            backend.close()
        if mapping is not None:
            gc.collect()  # Release Unicorn callback cycles and exported RAM views.
            mapping.close()
        if shared_created:
            args.shared_ram.unlink(missing_ok=True)
        if trace:
            trace.close()
        if bound:
            args.socket.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
