"""Package the measured stopped-card PCI D3hot/D0 cycle for the probe VM."""
import argparse
import json
from pathlib import Path


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'd0.bin').write_bytes(b'\0\0')
    (output / 'd3.bin').write_bytes(b'\3\0')
    (output / 'identity.bin').write_bytes(bytes.fromhex('331113e0'))
    script = '''#!/bin/sh
set -eu
config=/sys/bus/pci/devices/0000:00:04.0/config
/bin/busybox dd if="$config" of=/tmp/bri-pci-id bs=1 count=4
/bin/busybox cmp /tmp/bri-pci-id /bri-power/identity.bin
/bin/busybox dd if="$config" of=/tmp/bri-pmcsr bs=1 skip=68 count=2
/bin/busybox cmp /tmp/bri-pmcsr /bri-power/d0.bin
restore() {
    /bin/busybox dd if=/bri-power/d0.bin of="$config" bs=1 seek=68 count=2 conv=notrunc
    /bin/busybox sleep 0.02
}
trap restore EXIT
/bin/busybox dd if=/bri-power/d3.bin of="$config" bs=1 seek=68 count=2 conv=notrunc
/bin/busybox sleep 0.01
/bin/busybox dd if="$config" of=/tmp/bri-pmcsr bs=1 skip=68 count=2
/bin/busybox cmp /tmp/bri-pmcsr /bri-power/d3.bin
echo BRI_POWER_D3_OK
restore
trap - EXIT
/bin/busybox dd if="$config" of=/tmp/bri-pmcsr bs=1 skip=68 count=2
/bin/busybox cmp /tmp/bri-pmcsr /bri-power/d0.bin
echo BRI_POWER_D0_OK
'''
    (output / 'run').write_text(script)
    (output / 'run').chmod(0o755)
    return {'power_states': [3, 0], 'bar_accesses_in_d3': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().out)))
