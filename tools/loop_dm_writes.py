"""Find which DM the PM 0x1cb9 loop accumulates into.

Session 191 left that loop as the only shared INFO-page address with a count
skew -- 39 iterations on a call that takes V.90, 1 on the Conexant's -- and so
the best candidate for what the branch reads.

Coverage count is the trigger: snapshot DM every sample (a memcpy), and diff
only on the samples where the count for 0x1cb9 rose. A control set is taken on
samples where it did not, because an INFO page mid-handshake rewrites plenty of
DM for reasons that have nothing to do with this loop -- without the control,
every busy word looks like a hit.
"""
import ctypes, sys, collections
from pathlib import Path
sys.path.insert(0, 'tools')
from eicon_mips_shim import create_native_mips_modem, ADSP

BUILD = Path('artifacts/eicon-dsp/build-117-926')
capture, stop, pc = Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3], 0)
data = capture.read_bytes()
card = create_native_mips_modem(BUILD/'kernel'/'0009-diva-server-pri-30m-kernel',
                                BUILD/'tikrnl'/'0258-tikrnl81.f34-task', 'pcmu',
                                force_info_after_v8=True, tx_prbs=True,
                                native_bearer_activation=True)
cpu = card.cpu
dm_ptr = ADSP.adsp2181_dm(cpu)
SIZE = 0x4000 * 2

def snap():
    return ctypes.string_at(ctypes.cast(dm_ptr, ctypes.c_void_p), SIZE)

def diff(a, b):
    out = []
    for i in range(0, SIZE, 2):
        if a[i] != b[i] or a[i+1] != b[i+1]:
            out.append(i // 2)
    return out

ADSP.adsp2181_coverage_clear(cpu)
gated = False
prev = snap()
last_count = 0
on_loop = collections.Counter()
off_loop = collections.Counter()
loop_samples = off_samples = 0

for index, code in enumerate(data):
    if index / 8000 > stop:
        break
    resident = card.resident
    want = (resident == 0x0260)
    if want != gated:
        ADSP.adsp2181_coverage_gate(cpu, 1 if want else 0)
        gated = want
    card.frame_fast(code, index)
    if not want:
        prev = snap()
        continue
    count = ADSP.adsp2181_coverage_count(cpu, pc)
    cur = snap()
    if count != last_count:
        loop_samples += 1
        for a in diff(prev, cur):
            on_loop[a] += 1
        last_count = count
    elif off_samples < 400:
        off_samples += 1
        for a in diff(prev, cur):
            off_loop[a] += 1
    prev = cur

print(f'{capture.name}: PM {pc:#06x} fired on {loop_samples} samples; '
      f'control {off_samples} samples')
only = [(a, n) for a, n in on_loop.items() if a not in off_loop]
only.sort(key=lambda t: -t[1])
print(f'DM written on loop samples and never on control samples ({len(only)}):')
for a, n in only[:30]:
    print(f'  DM {a:#06x}  on {n}/{loop_samples} loop samples')
