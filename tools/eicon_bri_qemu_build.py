"""Install and build the Diva PCI frontend in a supplied QEMU 11.1.1 tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def build(source, output, python, ninja, jobs=4):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not 1 <= jobs <= 64 or source == output:
        raise ValueError('use a separate build directory and 1..64 jobs')
    if (source / 'VERSION').read_text().strip() != '11.1.1':
        raise ValueError('the PCI frontend is currently verified with QEMU 11.1.1')
    frontend = Path(__file__).resolve().parents[1] / 'qemu/diva-bri.c'
    shutil.copy2(frontend, source / 'hw/misc/diva-bri.c')
    meson = source / 'hw/misc/meson.build'
    text = meson.read_text()
    declaration = "system_ss.add(when: 'CONFIG_PCI', if_true: files('diva-bri.c'))"
    if declaration not in text:
        meson.write_text(text + '\n' + declaration + '\n')
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment['PATH'] = str(Path(ninja).resolve().parent) + os.pathsep + environment.get('PATH', '')
    subprocess.run([str(source / 'configure'), f'--python={python}', f'--ninja={ninja}',
        '--target-list=x86_64-softmmu', '--without-default-features', '--enable-tcg',
        '--disable-download', '--disable-docs', '--disable-tools', '--disable-user',
        '--disable-guest-agent', '--disable-werror'], cwd=output, env=environment, check=True)
    subprocess.run([str(ninja), '-j', str(jobs), 'qemu-system-x86_64'],
                   cwd=output, env=environment, check=True)
    executable = output / 'qemu-system-x86_64'
    devices = subprocess.check_output([str(executable), '-device', 'help'], text=True)
    if 'name "diva-bri"' not in devices:
        raise RuntimeError('built QEMU does not contain the Diva frontend')
    report = dict(qemu=str(executable), version='11.1.1',
                  frontend_sha256=hashlib.sha256(frontend.read_bytes()).hexdigest())
    (output / 'bri-build.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--ninja', type=Path, default=shutil.which('ninja'))
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if not args.ninja:
        parser.error('ninja is required; pass --ninja or add it to PATH')
    print(json.dumps(build(args.source, args.build, args.python, args.ninja, args.jobs)))
