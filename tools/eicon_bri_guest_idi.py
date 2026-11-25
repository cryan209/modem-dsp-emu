"""Prepare real userspace IDI transactions for the stock Linux diva_idi driver.

ABI: Linux v4.9 eicon/{um_xdi.h,um_idi.c,divasi.c}. The first four-byte
write selects an adapter; subsequent writes contain the 16-byte request
header. Responses have a 152-byte header, including a 144-byte union.
Only initialized response fields are compared; union padding is ignored.
"""
import argparse
import json
from pathlib import Path
import struct
import eicon_idi as idi

REQUEST = struct.Struct('<4I')
INDICATION_SIZE = 152
SIG_REQUEST = 0x20000002


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for adapter in range(1, 5):
        (output / f'adapter-{adapter}.bin').write_bytes(struct.pack('<I', adapter))
    packets = {'features.bin': REQUEST.pack(1, 0, 0, 0)}
    for name, request, payload in (
            ('assign.bin', idi.ASSIGN, idi.sig_assign_payload()),
            ('remove.bin', idi.REMOVE, b'')):
        packets[name] = REQUEST.pack(SIG_REQUEST, request, 0, len(payload)) + payload
    for name, data in packets.items():
        (output / name).write_bytes(data)
    (output / 'assign-rc.bin').write_bytes(struct.pack('<3I', 3, idi.ASSIGN_OK, 0))
    (output / 'remove-rc.bin').write_bytes(struct.pack('<3I', 3, idi.RC_OK, 0))
    (output / 'zero.bin').write_bytes(bytes(4))
    (output / 'features-type.bin').write_bytes(struct.pack('<I', 1))
    (output / 'channels.bin').write_bytes(struct.pack('<I', 2))
    script = '''#!/bin/sh
set -eu
modules=/lib/modules/$(uname -r)/kernel/drivers/isdn
/bin/busybox insmod "$modules/hardware/eicon/diva_idi.ko"
major=
while read number name; do
    if [ "$name" = DivasIDI ]; then major=$number; break; fi
done </proc/devices
[ -n "$major" ]
mknod /dev/DivasIDI c "$major" 0
read_response() {
    tries=0
    while [ "$tries" -lt 200 ]; do
        if /bin/busybox dd bs=4096 count=1 <&4 >/tmp/idi-response 2>/dev/null; then
            if [ -s /tmp/idi-response ]; then
                [ "$(/bin/busybox wc -c </tmp/idi-response)" -eq 152 ] || return 1
                /bin/busybox dd if=/tmp/idi-response of=/tmp/idi-length bs=1 skip=148 count=4 2>/dev/null
                /bin/busybox cmp /tmp/idi-length /bri-idi/zero.bin || return 1
                return 0
            fi
        fi
        tries=$((tries + 1))
        /bin/busybox sleep 0.01
    done
    echo "BRI_IDI_TIMEOUT adapter=$adapter"
    return 1
}
check_rc() {
    read_response
    /bin/busybox dd if=/tmp/idi-response of=/tmp/idi-fields bs=1 count=12 2>/dev/null
    /bin/busybox cmp /tmp/idi-fields "$1"
}
for adapter in 1 2 3 4; do
    exec 4<>/dev/DivasIDI
    /bin/busybox dd if=/bri-idi/adapter-$adapter.bin bs=4 count=1 >&4
    /bin/busybox dd if=/bri-idi/features.bin bs=16 count=1 >&4
    read_response
    /bin/busybox dd if=/tmp/idi-response of=/tmp/idi-type bs=1 count=4 2>/dev/null
    /bin/busybox cmp /tmp/idi-type /bri-idi/features-type.bin
    /bin/busybox dd if=/tmp/idi-response of=/tmp/idi-channels bs=1 skip=12 count=4 2>/dev/null
    /bin/busybox cmp /tmp/idi-channels /bri-idi/channels.bin
    echo "BRI_IDI_FEATURES_OK adapter=$adapter"
    for cycle in 1 2; do
        /bin/busybox dd if=/bri-idi/assign.bin bs=ASSIGN_SIZE count=1 >&4
        check_rc /bri-idi/assign-rc.bin
        echo "BRI_IDI_ASSIGN_OK adapter=$adapter cycle=$cycle"
        /bin/busybox dd if=/bri-idi/remove.bin bs=16 count=1 >&4
        check_rc /bri-idi/remove-rc.bin
        echo "BRI_IDI_REMOVE_OK adapter=$adapter cycle=$cycle"
    done
    exec 4>&-
done
for info in /proc/net/eicon/adapter*/info; do
    echo "BRI_IDI_ADAPTER_INFO $info"
    cat "$info"
done
cat /proc/interrupts
echo BRI_IDI_OK
'''.replace('ASSIGN_SIZE', str(len(packets['assign.bin'])))
    (output / 'run').write_text(script)
    (output / 'run').chmod(0o755)
    manifest = dict(adapters=[1, 2, 3, 4], cycles=2, entity='signalling',
                    request_header_size=REQUEST.size, indication_header_size=INDICATION_SIZE)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.out)))
