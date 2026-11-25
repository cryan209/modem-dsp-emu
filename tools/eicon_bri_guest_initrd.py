"""Pack an existing Linux initramfs tree with the isolated BRI probe init.

Reads the supplied tree only. Device-node CPIO metadata is emitted directly,
so building the guest never needs root or creates host device nodes.
"""
import argparse
import gzip
import os
from pathlib import Path
import stat


def build(root, output, init=None):
    root = Path(root)
    init = Path(init) if init is not None else Path(__file__).resolve().parents[1] / 'qemu/guest-init'
    if not (root / 'bin/busybox').is_file():
        raise ValueError('guest tree must contain its own Linux BusyBox')
    inode = 0
    with open(output, 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as stream:
        def entry(name, mode, data=b'', major=0, minor=0):
            nonlocal inode
            inode += 1
            filename = name.encode() + b'\0'
            fields = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(filename), 0)
            header = b'070701' + ''.join(f'{field:08x}' for field in fields).encode()
            stream.write(header + filename)
            stream.write(bytes(-(len(header) + len(filename)) % 4))
            stream.write(data)
            stream.write(bytes(-len(data) % 4))
        for path in sorted(root.rglob('*')):
            name = path.relative_to(root).as_posix()
            if name in ('dev/console', 'dev/null', 'bri-init'):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                entry(name, mode, os.readlink(path).encode())
            elif stat.S_ISREG(mode):
                entry(name, mode, path.read_bytes())
            elif stat.S_ISDIR(mode):
                entry(name, mode)
        entry('dev/console', stat.S_IFCHR | 0o600, major=5, minor=1)
        entry('dev/null', stat.S_IFCHR | 0o666, major=1, minor=3)
        entry('bri-init', stat.S_IFREG | 0o755, init.read_bytes())
        entry('TRAILER!!!', 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--init', type=Path, help='alternate isolated-guest init script')
    args = parser.parse_args()
    build(args.root, args.out, args.init)
