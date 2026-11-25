"""Compile a CAPI module or freestanding client in an offline Linux build guest.

The prepared initramfs contains the compiler, matching headers and source.
The only attached disk is a new temporary 16 MiB output file. No emulated
BRI endpoint, physical passthrough, network or host share is attached.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time


def compile_artifact(qemu, kernel, initrd, output, timeout=1200, kind='module'):
    if kind not in ('module', 'executable'):
        raise ValueError('invalid build artifact kind')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    serial = output.with_suffix('.serial.log')
    report = dict(completed=False,
                  kernel_sha256=hashlib.sha256(Path(kernel).read_bytes()).hexdigest(),
                  initrd_sha256=hashlib.sha256(Path(initrd).read_bytes()).hexdigest())
    process = None
    with tempfile.TemporaryDirectory(prefix='bri-build-', dir='/private/tmp') as directory, \
            output.with_suffix('.qemu.log').open('w') as log:
        disk = Path(directory) / 'module-output.raw'
        with disk.open('xb') as stream:
            stream.truncate(16 * 1024 * 1024)
        try:
            process = subprocess.Popen([str(qemu), '-machine', 'pc', '-accel', 'tcg',
                '-nodefaults', '-display', 'none', '-m', '1536M', '-no-reboot',
                '-icount', 'shift=0,sleep=off', '-serial', f'file:{serial}',
                '-kernel', str(kernel), '-initrd', str(initrd),
                '-append', 'console=ttyS0,115200 rdinit=/bri-init bri_build=1 panic=-1',
                '-drive', f'file={disk},format=raw,if=none,id=moduleout',
                '-device', 'virtio-blk-pci,drive=moduleout'],
                stdout=subprocess.DEVNULL, stderr=log)
            deadline = time.monotonic() + timeout
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            report['frontend_exit'] = process.poll()
            if process.poll() is None:
                report['timeout'] = True
            text = serial.read_text(errors='replace') if serial.exists() else ''
            marker = 'MODULE' if kind == 'module' else 'EXECUTABLE'
            matches = re.findall(r'^BRI_BUILD_' + marker + r'_BYTES=(\d+)\s*$', text, re.MULTILINE)
            if 'BRI_BUILD_OK' in text and len(matches) == 1 and process.poll() == 0:
                count = int(matches[0])
                if not 0 < count < 16 * 1024 * 1024:
                    raise ValueError('invalid exported module size')
                with disk.open('rb') as stream:
                    data = stream.read(count)
                if (len(data) != count or count < 20 or data[:7] != b'\x7fELF\x02\x01\x01'
                        or struct.unpack_from('<HH', data, 16) != (1 if kind == 'module' else 2, 62)):
                    raise ValueError('invalid exported x86-64 build artifact')
                target = output.with_suffix('.ko' if kind == 'module' else '.elf')
                target.write_bytes(data)
                report.update(completed=True, artifact_type=kind, artifact=str(target), artifact_bytes=count,
                              artifact_sha256=hashlib.sha256(data).hexdigest())
                if kind == 'module':
                    report.update(module=str(target), module_bytes=count,
                                  module_sha256=report['artifact_sha256'])
            return report
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--initrd', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--kind', choices=('module', 'executable'), default='module')
    args = parser.parse_args()
    result = compile_artifact(args.qemu, args.kernel, args.initrd, args.out, args.timeout, args.kind)
    print(json.dumps(result))
    raise SystemExit(0 if result['completed'] else 1)
