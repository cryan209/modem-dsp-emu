"""Probe native MIPS JIT access out of process before the full test suite.

An import alone is insufficient: restricted hosts can terminate Unicorn with
SIGILL during memory mapping. Never run that probe inside pytest's process.
"""
import subprocess
import sys


PROBE = """
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import UC_MIPS_REG_V0
u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
u.mem_map(0x1000, 4096)
u.mem_write(0x1000, bytes.fromhex('01000224'))  # addiu v0, zero, 1
u.emu_start(0x1000, 0x1004, count=1)
assert u.reg_read(UC_MIPS_REG_V0) == 1
"""


def main():
    try:
        result = subprocess.run([sys.executable, '-c', PROBE],
                                capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        print('Native MIPS probe timed out; use make test for the portable gate.',
              file=sys.stderr)
        return 1
    if result.returncode:
        reason = (f'signal {-result.returncode}' if result.returncode < 0
                  else f'exit {result.returncode}')
        print(f'Native MIPS probe failed ({reason}). The full suite requires '
              'native JIT access; use make test on restricted hosts.', file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1
    print('Native Unicorn MIPS mapping/execution probe: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
