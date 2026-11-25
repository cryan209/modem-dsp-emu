"""Build the freestanding standard-CAPI guest probe using clang's assembler.

The single-section, position-independent ELF object has no relocations or
runtime dependencies. Package it in an ordinary x86-64 Linux executable;
reject all allocated sections other than .text and any text relocations.
The binary runs only inside the isolated guest, not on the host.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def executable(data):
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    if header[:4] != (b'\x7fELF\x02\x01\x01' + bytes(9), 1, 62, 1):
        raise ValueError('expected a little-endian x86-64 ELF relocatable object')
    shoff, entsize, count, strings = header[6], header[11], header[12], header[13]
    if entsize != 64 or shoff + count * entsize > len(data):
        raise ValueError('invalid ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + i * entsize) for i in range(count)]
    names = sections[strings]
    names = data[names[4]:names[4] + names[5]]
    texts = [i for i, s in enumerate(sections) if names[s[0]:].split(b'\0')[0] == b'.text']
    if len(texts) != 1:
        raise ValueError('expected one text section')
    index = texts[0]
    for i, section in enumerate(sections):
        if section[1] in (4, 9) and section[7] == index and section[5]:
            raise ValueError('guest object has unresolved text relocations')
        if i != index and section[2] & 2 and section[5]:
            raise ValueError('guest object has other allocated sections')
    section = sections[index]
    text = data[section[4]:section[4] + section[5]]
    if not text or len(text) != section[5]:
        raise ValueError('invalid text section')
    size = 4096 + len(text)
    ident = b'\x7fELF\x02\x01\x01' + bytes(9)
    elf = struct.pack('<16sHHIQQQIHHHHHH', ident, 2, 62, 1, 0x401000,
                      64, 0, 0, 64, 56, 2, 0, 0, 0)
    elf += struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, size, size, 4096)
    elf += struct.pack('<IIQQQQQQ', 0x6474e551, 6, 0, 0, 0, 0, 0, 16)
    return elf + bytes(4096 - len(elf)) + text


def build(output, clang, vendor_module=None, call_client=None, call_count=4, call_repeats=2):
    if call_count not in (1, 2, 3, 4):
        raise ValueError('call count must be 1..4')
    if not 1 <= call_repeats <= 64:
        raise ValueError('call repeats must be 1..64')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'qemu/guest-capi.S'
    if call_client is None:
        with tempfile.TemporaryDirectory(prefix='bri-capi-') as directory:
            obj = Path(directory) / 'client.o'
            subprocess.run([str(clang), '-target', 'x86_64-linux-gnu', '-c', str(source), '-o', str(obj)], check=True)
            client = executable(obj.read_bytes())
    else:
        client = Path(call_client).read_bytes()
        if (len(client) < 20 or client[:7] != b'\x7fELF\x02\x01\x01'
                or struct.unpack_from('<HH', client, 16) != (2, 62)):
            raise ValueError('expected an x86-64 executable call client')
        source = ROOT / 'qemu/guest-capi-call.c'
    (output / 'client').write_bytes(client)
    (output / 'client').chmod(0o755)
    if vendor_module is not None:
        module_data = Path(vendor_module).read_bytes()
        if (len(module_data) < 20 or module_data[:7] != b'\x7fELF\x02\x01\x01'
                or struct.unpack_from('<HH', module_data, 16) != (1, 62)):
            raise ValueError('expected an x86-64 vendor kernel module')
        (output / 'driver.ko').write_bytes(module_data)
    script = '''#!/bin/sh
set -eu
modules=/lib/modules/$(uname -r)/kernel/drivers/isdn
LOAD_CAPI_DRIVER
major=
while read number name; do
    if [ "$name" = capi20 ]; then major=$number; break; fi
done </proc/devices
[ -n "$major" ]
if [ ! -c /dev/capi20 ]; then
    mknod /dev/capi20 c "$major" 0
fi
CAPI_CONTROLLER_INFO
if /bri-capi/client; then
    echo BRI_CAPI_OK
else
    status=$?
    echo "BRI_CAPI_FAILED status=$status"
    exit "$status"
fi
'''
    if vendor_module is not None:
        script = script.replace('LOAD_CAPI_DRIVER', '/bin/busybox insmod /bri-capi/driver.ko\necho BRI_CAPI_DRIVER vendor-optimized')
        script = script.replace('CAPI_CONTROLLER_INFO', '/sbin/modinfo /bri-capi/driver.ko')
    else:
        script = script.replace('LOAD_CAPI_DRIVER', '/bin/busybox insmod \"$modules/hardware/eicon/divacapi.ko\"\n/bin/busybox insmod \"$modules/capi/capi.ko\"\necho BRI_CAPI_DRIVER stock-kernel')
        script = script.replace('CAPI_CONTROLLER_INFO', 'cat /proc/capi/controller')
    if call_client is not None:
        start = script.index('if /bri-capi/client; then')
        script = script[:start] + 'for round in ' + ' '.join(str(i) for i in range(1, call_repeats + 1)) + '; do\n' + f"""    if /bri-capi/client {call_count} "$round"; then
        /bin/busybox sleep 0.1
        for info in /proc/net/eicon/adapter*/info; do
            echo "BRI_CAPI_RELEASE_INFO round=$round path=$info"
            cat "$info"
        done
        cat /proc/interrupts
        echo "BRI_CAPI_RELEASE_OK round=$round"
    else
        status=$?
        echo "BRI_CAPI_FAILED status=$status"
        exit "$status"
    fi
done
echo BRI_CAPI_OK
"""
    (output / 'run').write_text(script)
    (output / 'run').chmod(0o755)
    report = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  client_sha256=hashlib.sha256(client).hexdigest(),
                  architecture='x86_64-linux-gnu', controllers=4, b_channels_per_controller=2,
                  driver='vendor-optimized' if vendor_module is not None else 'stock-kernel')
    if call_client is not None:
        report['call_count'] = call_count
        report['call_repeats'] = call_repeats
        report['startup_sha256'] = hashlib.sha256((ROOT / 'qemu/guest-capi-call-start.S').read_bytes()).hexdigest()
    if vendor_module is not None:
        report['driver_sha256'] = hashlib.sha256(module_data).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--clang', type=Path, default=shutil.which('clang'))
    parser.add_argument('--vendor-module', type=Path, help='test the vendor default CAPI module instead of the archived stock driver')
    parser.add_argument('--call-client', type=Path)
    parser.add_argument('--call-count', type=int, default=4)
    parser.add_argument('--call-repeats', type=int, default=2)
    args = parser.parse_args()
    if not args.clang and not args.call_client:
        parser.error('clang is required')
    print(json.dumps(build(args.out, args.clang, args.vendor_module, args.call_client, args.call_count, args.call_repeats)))
