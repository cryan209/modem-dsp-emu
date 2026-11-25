"""Prepare an offline Linux compiler initramfs tree for the vendor CAPI module.

Archive inputs are SHA-256 pinned. They are unpacked on the host; package
scripts and Linux executables run only in the disposable build guest.
Requires libarchive tar (bsdtar), ar, and curl. Driver source is unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def prepare(installer, source, output, cache, tar):
    installer, source, output, cache = map(Path, (installer, source, output, cache))
    if output.exists():
        raise FileExistsError(output)
    if not (source / 'Makefile').is_file() or '#define DIVA_EICON_CAPI 1' not in (source / 'platform.h').read_text():
        raise ValueError('expected the vendor default optimized-CAPI source tree')
    version = subprocess.check_output([str(tar), '--version'], text=True)
    if 'libarchive' not in version:
        raise ValueError('use libarchive tar/bsdtar for the Debian archive inputs')
    manifest = json.loads((ROOT / 'qemu/debian-stretch-build-packages.json').read_text())
    cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bri-build-root-', dir=output.parent) as directory:
        target = Path(directory) / 'root'
        target.mkdir()
        subprocess.run([str(tar), '-xf', str(installer.resolve()), '-C', str(target),
                        '--exclude=dev/console', '--exclude=dev/null'], check=True)
        for name, data in sorted(manifest.items()):
            path = cache / Path(data['Filename']).name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != data['SHA256']:
                part = path.with_suffix('.part')
                subprocess.run(['curl', '-fLsS', '--max-time', '60',
                    'https://archive.debian.org/debian/' + data['Filename'], '-o', str(part)], check=True)
                if hashlib.sha256(part.read_bytes()).hexdigest() != data['SHA256']:
                    raise ValueError(f'{name}: package checksum mismatch')
                part.replace(path)
            members = subprocess.check_output(['ar', 't', str(path)], text=True).splitlines()
            member = next(value for value in members if value.startswith('data.tar'))
            process = subprocess.Popen(['ar', 'p', str(path), member], stdout=subprocess.PIPE)
            try:
                subprocess.run([str(tar), '--safe-writes', '-xf', '-', '-C', str(target)],
                               stdin=process.stdout, check=True)
            finally:
                process.stdout.close()
                status = process.wait()
            if status:
                raise RuntimeError(f'{name}: ar failed with {status}')
            print(f'{name}: verified and unpacked', flush=True)
        for name, link in [('awk', 'mawk'), ('gcc', 'gcc-6'), ('cc', 'gcc-6')]:
            path = target / 'usr/bin' / name
            if not path.exists() and not path.is_symlink():
                path.symlink_to(link)
        destination = target / 'bri-build/kernel'
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns(
            '*.o', '*.ko', '*.cmd', '.git', '.tmp_versions', 'Module.symvers', 'modules.order'))
        compat = target / 'bri-build/compat/linux'
        compat.mkdir(parents=True)
        shutil.copy2(ROOT / 'qemu/kernel-compat/linux/stdarg.h', compat / 'stdarg.h')
        files = {path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(destination.rglob('*')) if path.is_file()}
        report = dict(packages=len(manifest), source_sha256=files,
                      configuration='vendor default optimized CAPI', modified_driver_files=[],
                      installer_sha256=hashlib.sha256(installer.read_bytes()).hexdigest())
        (target / 'bri-build/environment.json').write_text(json.dumps(report, indent=2) + '\n')
        if output.exists():
            raise FileExistsError(output)
        target.rename(output)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer-initrd', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True, help='vendor kernel source directory')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--tar', type=Path, default=shutil.which('bsdtar') or shutil.which('tar'))
    args = parser.parse_args()
    report = prepare(args.installer_initrd, args.source, args.out, args.cache, args.tar)
    print(json.dumps(dict(packages=report['packages'], source_files=len(report['source_sha256']))))
