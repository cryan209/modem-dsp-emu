"""Verify Diva PCI discovery, BARs, VPD, INTA and firmware boot in QEMU.

Requires QEMU built with qemu/diva-bri.c. This is bus integration evidence,
not a claim that a guest kernel driver has attached. Diagnostic firmware
staging is explicit and still executes the unmodified MIPS/DSP images.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


class QTest:
    def __init__(self, connection):
        self.stream = connection.makefile('rwb', buffering=0)
        self.irqs = []

    def command(self, command):
        self.stream.write(command.encode() + b'\n')
        while True:
            line = self.stream.readline().decode().strip()
            if line.startswith('IRQ '):
                self.irqs.append(line)
                continue
            if not line.startswith('OK'):
                raise RuntimeError(f'qtest {command!r}: {line!r}')
            fields = line.split()
            return int(fields[1], 0) if len(fields) > 1 else None

    def pci_read(self, offset, size=4, slot=4, function=0):
        self.command(f'outl 0xcf8 {0x80000000 | slot << 11 | function << 8 | offset & ~3:#x}')
        return self.command(f'in{ {1:"b", 2:"w", 4:"l"}[size]} {0xcfc + (offset & 3):#x}')

    def pci_write(self, offset, value, size=4, slot=4, function=0):
        self.command(f'outl 0xcf8 {0x80000000 | slot << 11 | function << 8 | offset & ~3:#x}')
        self.command(f'out{ {1:"b", 2:"w", 4:"l"}[size]} {0xcfc + (offset & 3):#x} {value:#x}')


def wait_socket(path, process):
    deadline = time.monotonic() + 20
    while not path.exists():
        if process.poll() is not None:
            raise RuntimeError(f'process exited before creating {path.name}: {process.returncode}')
        if time.monotonic() > deadline:
            raise TimeoutError(f'waiting for {path.name}')
        time.sleep(0.01)


def verify(qemu, output, firmware=None, shared_ram=False):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rpc_path = output.with_suffix('.rpc.jsonl')
    backend = frontend = None
    report = dict(guest_driver_attached=False, firmware_staged=firmware is not None,
                  shared_sdram=shared_ram,
                  qemu=str(qemu), completed=False)
    with tempfile.TemporaryDirectory(prefix='bri-qtest-', dir='/private/tmp') as temporary, \
            output.with_suffix('.backend.log').open('w') as backend_log, \
            output.with_suffix('.qemu.log').open('w') as qemu_log:
        directory = Path(temporary)
        board_socket, test_socket = directory / 'bri.sock', directory / 'test.sock'
        ram_file = directory / 'sdram.bin'
        try:
            command = [sys.executable, str(ROOT / 'tools/eicon_bri_rpc.py'),
                       '--socket', str(board_socket), '--trace', str(rpc_path)]
            if firmware:
                command += ['--stage-firmware', str(firmware)]
            if shared_ram:
                command += ['--shared-ram', str(ram_file)]
            backend = subprocess.Popen(command, cwd=ROOT, stdout=backend_log, stderr=subprocess.STDOUT)
            wait_socket(board_socket, backend)
            device = f'diva-bri,id=bri0,addr=4,socket={board_socket}'
            if shared_ram:
                device += f',ram-file={ram_file}'
            frontend = subprocess.Popen([str(qemu), '-machine', 'pc', '-accel', 'qtest',
                '-nodefaults', '-display', 'none', '-m', '64M',
                '-qtest', f'unix:{test_socket},server=on,wait=off', '-qtest-log', '/dev/null',
                '-device', device],
                stdout=subprocess.DEVNULL, stderr=qemu_log)
            wait_socket(test_socket, frontend)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(30)
                connection.connect(str(test_socket))
                test = QTest(connection)
                report['identity'] = test.pci_read(0)
                report['subsystem'] = test.pci_read(0x2c)
                assert report['identity'] == 0xe0131133
                assert report['subsystem'] == 0x00148001
                report['bar_masks'] = []
                for index, size in enumerate((256, 256, 0x1000000, 0x10000)):
                    test.pci_write(0x10 + index * 4, 0xffffffff)
                    mask = test.pci_read(0x10 + index * 4)
                    address_mask = 0xfffffffc if index == 1 else 0xfffffff0
                    assert ((~(mask & address_mask) + 1) & 0xffffffff) == size
                    report['bar_masks'].append(mask)
                for index, base in enumerate((0xf1000000, 0xc001, 0xe0000000, 0xf2000000)):
                    test.pci_write(0x10 + index * 4, base)
                test.pci_write(4, 7, size=2)
                test.pci_write(0x4e, 252, size=2)
                assert test.pci_read(0x4e, size=2) == 0x80fc
                report['serial'] = test.pci_read(0x50)
                assert report['serial'] == 12196
                # Use spare SDRAM, not the staged firmware's low headers.
                test.command('writeq 0xe0f80000 0x1122334455667788')
                report['wide_memory_read'] = test.command('readq 0xe0f80000')
                assert report['wide_memory_read'] == 0x1122334455667788
                # Route all PIIX PIRQ links to IRQ11 and observe IOAPIC input.
                test.pci_write(0x60, 0x0b0b0b0b, slot=1)
                test.command('irq_intercept_in ioapic')
                test.command('writeb 0xf1000069 9')
                test.command('writel 0xf2000400 1')
                assert test.command('readb 0xf1000069') & 0x80
                test.command('writel 0xf2000400 0')
                assert not test.command('readb 0xf1000069') & 0x80
                assert 'IRQ raise 11' in test.irqs and 'IRQ lower 11' in test.irqs, test.irqs
                report['irq_events'] = list(test.irqs)
                # PLX reset must remove even the direct shared-RAM aperture.
                test.command('writeb 0xf1000069 0')
                saved = test.command('readl 0xe0080000')
                test.command('writel 0xe0080000 0x21495242')
                pins = test.command('readw 0xf100006e')
                test.command(f'writew 0xf100006e {pins | 0x4000:#x}')
                test.command(f'writew 0xf100006e {pins:#x}')
                assert test.command('readl 0xf1000000') == 0xfff00000
                assert test.command('readl 0xe0080000') == 0xffffffff
                assert test.command('readl 0xf2000200') == 0xffffffff
                test.command('writel 0xe0080000 0xdeadbeef')
                test.command(f'writew 0xf100006e {pins | 0x2000:#x}')
                test.command(f'writew 0xf100006e {pins:#x}')
                assert test.command('readl 0xf1000000') == 0xff000000
                assert test.command('readl 0xe0080000') == 0x21495242
                assert test.command('readl 0xf2000200') == 0
                assert test.command('readw 0xf100006e') == pins
                test.command(f'writel 0xe0080000 {saved:#x}')
                test.command('writeb 0xf1000069 9')
                report['plx_reset_reload'] = True
                # Only configuration accesses while in D3, as on the spare.
                saved = test.command('readl 0xe0080000')
                test.command('writel 0xe0080000 0x33524942')
                config = [test.pci_read(offset) for offset in (0, 4, 0x10, 0x14, 0x18, 0x1c)]
                for size in (1, 2, 4):
                    test.pci_write(0x44, 3, size=size)
                    assert test.pci_read(0x44, size=2) == 3
                    assert [test.pci_read(offset) for offset in (0, 4, 0x10, 0x14, 0x18, 0x1c)] == config
                    test.pci_write(0x44, 0, size=size)
                    assert test.pci_read(0x44, size=2) == 0
                    assert test.command('readl 0xe0080000') == 0x33524942
                    assert test.command('readw 0xf100006e') == pins
                    assert test.command('readl 0xf1000000') == 0xff000000
                for state in (1, 2):
                    test.pci_write(0x44, state, size=2)
                    assert test.pci_read(0x44, size=2) == 0
                test.command(f'writel 0xe0080000 {saved:#x}')
                irq_start = len(test.irqs)
                test.command('writel 0xf2000400 1')
                assert test.command('readb 0xf1000069') == 0x89
                test.pci_write(0x44, 3, size=2)
                assert test.pci_read(0x44, size=2) == 3
                test.pci_write(0x44, 0, size=2)
                assert test.pci_read(0x44, size=2) == 0
                assert test.command('readl 0xf2000400') == 0
                assert test.command('readb 0xf1000069') == 9
                report['power_irq_events'] = test.irqs[irq_start:]
                assert report['power_irq_events'] == ['IRQ raise 11', 'IRQ lower 11']
                report['pci_power_cycle'] = True
                if firmware:
                    test.command('writel 0xf2000200 3')
                    signatures = []
                    for step in range(40):
                        test.command('clock_step 20000000')
                        signatures = [test.command(f'readw {0xe0000000 + 0x1000 + i * 0x11000 + 0x1e:#x}')
                                      for i in range(4)]
                        if signatures == [0x4447] * 4:
                            break
                    assert signatures == [0x4447] * 4, signatures
                    report['signatures'] = signatures
                    report['virtual_boot_ns'] = (step + 1) * 20000000
                test.stream.close()
            records = [json.loads(line) for line in rpc_path.read_text().splitlines()]
            assert all(record['status'] == 0 for record in records)
            report['rpc_count'] = len(records)
            report['completed'] = True
        finally:
            if frontend and frontend.poll() is None:
                frontend.terminate()
                frontend.wait(timeout=10)
            if backend and backend.poll() is None:
                try:
                    backend.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    backend.terminate()
                    backend.wait(timeout=5)
            output.write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--firmware-dir', type=Path)
    parser.add_argument('--shared-ram', action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify(args.qemu, args.out, args.firmware_dir, args.shared_ram)))
