"""Boot an isolated Linux guest and verify its stock Diva PCI driver binds.

No NIC, disks, physical PCI passthrough or host shares are attached. Board
RAM starts empty; this probe checks genuine driver attachment separately
from diagnostic firmware staging in the QEMU qtest probe. Optional legacy
load packets exercise guest-side writes and START; --require-firmware
checks their real four-controller/eight-DSP boot result. --require-idi
adds stock-driver signalling lifecycle checks; --require-capi requires
all controller profiles and LISTEN confirmations (the archived 64-bit
CAPI driver fails this diagnostic; the vendor-default module passes).
--require-calls adds full 2048-byte call/data/teardown and resource checks
for the explicit external TE/NT wire fixture.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zlib
from eicon_bri_qtest import ROOT, wait_socket


def inspect_guest(text, board):
    """Require driver, firmware, resource and IRQ evidence independently."""
    report = {'board': board}
    report['guest_driver_attached'] = ('BRI_DRIVER_ATTACH_OK' in text
                                        and 'BRI_DRIVER_BOUND' in text and '12196' in text)
    controllers = []
    for block in text.split('BRI_ADAPTER_INFO ')[1:]:
        serial = re.search(r'^Serial\s*:\s*([^\r\n]+)', block, re.MULTILINE)
        state = re.search(r'^State\s*:\s*([^\r\n]+)', block, re.MULTILINE)
        if serial and state:
            controllers.append(dict(serial=serial.group(1).strip(), state=state.group(1).strip()))
    report['controllers'] = controllers
    fpga = board.get('fpga', {})
    report['guest_fpga'] = ('BRI_FPGA_DOWNLOAD_OK' in text and fpga.get('done') is True
        and fpga.get('clocks') == 233652 and fpga.get('length_count') == 233649
        and fpga.get('personality') == 'diva-4bri-v2-20071015' and not fpga.get('error'))
    power_markers = [text.find(marker) for marker in (
        'BRI_POWER_D3_OK', 'BRI_POWER_D0_OK', 'BRI_FIRMWARE_START_RETURNED')]
    report['guest_power_cycle'] = (all(index >= 0 for index in power_markers)
        and power_markers == sorted(power_markers) and board.get('power_state') == 0
        and board.get('power_transitions') == [3, 0] and not board.get('fault'))
    report['firmware_started'] = 'BRI_FIRMWARE_LOAD_OK' in text
    report['firmware_staged'] = 'BRI_FIRMWARE_START_RETURNED' in text
    report['guest_driver_attached'] &= [c['serial'] for c in controllers] == [
        '12196', '12196-2', '12196-3', '12196-4']
    post_start = text.split('BRI_FIRMWARE_START_RETURNED', 1)[-1].split('BRI_FIRMWARE_LOAD_OK', 1)[0]
    report['firmware_controllers'] = [dict(serial=serial.strip(), state=state.strip())
        for serial, state in zip(re.findall(r'^Serial\s*:\s*([^\r\n]+)', post_start, re.MULTILINE),
                                 re.findall(r'^State\s*:\s*([^\r\n]+)', post_start, re.MULTILINE))]
    report['firmware_started'] &= (bool(re.search(r'^State\s*:\s*active', post_start, re.MULTILINE))
        and [c['signature'] for c in report.get('board', {}).get('controllers', [])] == [0x4447] * 4
        and report.get('board', {}).get('allocated_dsps') == list(range(8))
        and report.get('board', {}).get('cpu_running') is True
        and report.get('board', {}).get('fault') is None)
    report['idi_features'] = [int(n) for n in re.findall(r'BRI_IDI_FEATURES_OK adapter=(\d+)', text)]
    report['idi_assign'] = [[int(a), int(c)] for a, c in re.findall(
        r'BRI_IDI_ASSIGN_OK adapter=(\d+) cycle=(\d+)', text)]
    report['idi_remove'] = [[int(a), int(c)] for a, c in re.findall(
        r'BRI_IDI_REMOVE_OK adapter=(\d+) cycle=(\d+)', text)]
    expected_cycles = [[a, c] for a in range(1, 5) for c in (1, 2)]
    irq_counts = [int(n) for n in re.findall(r'^\s*11:\s+(\d+).*DIVA 4BRI', text, re.MULTILINE)]
    report['guest_irq_counts'] = irq_counts
    idi_info = text.split('BRI_IDI_ADAPTER_INFO ')[1:]
    report['idi_entity_counts'] = [int(n) for block in idi_info for n in
        re.findall(r'^E\. max/used\s*:\s*\d+/(\d+)', block, re.MULTILINE)[:1]]
    idi_irq_counts = [int(n) for n in re.findall(r'^\s*11:\s+(\d+).*DIVA 4BRI',
        text.split('BRI_IDI_OK', 1)[0], re.MULTILINE)]
    report['idi_irq_counts'] = idi_irq_counts
    report['guest_idi'] = (report['firmware_started'] and 'BRI_IDI_OK' in text
        and report['idi_features'] == [1, 2, 3, 4]
        and report['idi_assign'] == report['idi_remove'] == expected_cycles
        and report['idi_entity_counts'] == [1, 1, 1, 1]
        and len(idi_irq_counts) >= 3 and idi_irq_counts[-1] > idi_irq_counts[-2])
    report['capi_driver'] = re.findall(r'BRI_CAPI_DRIVER ([a-z-]+)', text)
    report['capi_profiles'] = [int(n) for n in re.findall(r'BRI_CAPI_PROFILE_OK controller=(\d+)', text)]
    report['capi_listen'] = [int(n) for n in re.findall(r'BRI_CAPI_LISTEN_OK controller=(\d+)', text)]
    report['capi_errors'] = re.findall(r'BRI_CAPI_ERROR code=([0-9a-f]+)', text)
    report['capi_failure_stages'] = [int(n) for n in re.findall(r'BRI_CAPI_FAILED status=(\d+)', text)]
    runs = max(1, len(re.findall(r'^BRI_CAPI_CALL_BEGIN ', text, re.MULTILINE)))
    report['guest_capi'] = (report['firmware_started'] and 'BRI_CAPI_OK' in text
        and 'BRI_CAPI_CLIENT_OK' in text
        and report['capi_profiles'] == report['capi_listen'] == [1, 2, 3, 4] * runs
        and len(re.findall(r'^BRI_CAPI_CLIENT_OK\s*$', text, re.MULTILINE)) == runs
        and not report['capi_errors'] and not report['capi_failure_stages'])
    report.update(inspect_calls(text, board, report['guest_capi']))
    return report


def call_payload(endpoint, round_index):
    if endpoint not in range(8) or round_index not in range(1, 65):
        raise ValueError('invalid call payload endpoint/round')
    payload = bytearray((j * 37 + (endpoint + 1) * 11 + round_index * 17) & 255 for j in range(2048))
    payload[:16] = bytes((0, 255, 126, 125, 17, 19)) + b'BRICAPI' + bytes((128, 85, 170))
    payload[16:18] = bytes((endpoint + 1, round_index))
    return bytes(payload)


def inspect_calls(text, board, capi_ok):
    """Require full messages, teardown, IRQ activity and stable resources per run."""
    matches = list(re.finditer(r'^BRI_CAPI_CALL_BEGIN calls=(\d+) round=(\d+)', text, re.MULTILINE))
    runs = []
    for index, match in enumerate(matches):
        count, round_index = map(int, match.groups())
        region = text[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        expected = sorted({(i // 2) * 4 + i % 2 + offset for i in range(count) for offset in (0, 2)}) if count in range(1, 5) else []
        def endpoints(marker):
            return [int(n) for n in re.findall(marker + r' endpoint=(\d+)', region)]
        def entities(marker):
            return [int(n) for block in region.split(marker)[1:] for n in
                re.findall(r'^E\. max/used\s*:\s*\d+/(\d+)', block, re.MULTILINE)[:1]]
        received = [dict(endpoint=int(e), bytes=int(n), crc32=crc) for e, n, crc in
            re.findall(r'BRI_CAPI_DATA_OK endpoint=(\d+) bytes=(\d+) crc32=([0-9a-f]+)', region)]
        payloads_ok = bool(expected) and round_index in range(1, 65)
        for record in received:
            e = record['endpoint']
            if e not in expected or record['bytes'] != 2048 or not 1 <= round_index <= 64:
                payloads_ok = False
            else:
                payloads_ok &= record['crc32'] == f'{zlib.crc32(call_payload(e ^ 2, round_index)):08x}'
        before = [int(n) for n in re.findall(r'^\s*11:\s+(\d+).*DIVA 4BRI', text[:match.start()], re.MULTILINE)]
        after = [int(n) for n in re.findall(r'^\s*11:\s+(\d+).*DIVA 4BRI', region, re.MULTILINE)]
        run = dict(calls=count, round=round_index, received=received,
            bearers=endpoints('BRI_CAPI_BEARER_ACTIVE'), tx=endpoints('BRI_CAPI_TX_OK'),
            b3_cleared=endpoints('BRI_CAPI_B3_CLEARED'), signal_cleared=endpoints('BRI_CAPI_SIGNAL_CLEARED'),
            baseline_entities=entities('BRI_CAPI_BASELINE_INFO'), release_entities=entities('BRI_CAPI_RELEASE_INFO'),
            irq_before=before[-1] if before else None, irq_after=after[-1] if after else None)
        def position(pattern):
            found = list(re.finditer(r'^' + pattern + r'\r?$', region, re.MULTILINE))
            return found[0].start() if len(found) == 1 else -1
        connected = position('BRI_CAPI_CALLS_CONNECTED endpoints=' + str(len(expected)))
        media = position('BRI_CAPI_MEDIA_OK endpoints=' + str(len(expected)) + ' bytes=2048 round=' + str(round_index))
        teardown = position('BRI_CAPI_TEARDOWN_OK endpoints=' + str(len(expected)))
        released = position('BRI_CAPI_RELEASE_OK round=' + str(round_index))
        active_positions = [m.start() for m in re.finditer('BRI_CAPI_BEARER_ACTIVE endpoint=', region)]
        data_positions = [m.start() for m in re.finditer(r'BRI_CAPI_(?:DATA|TX)_OK endpoint=', region)]
        b3_positions = [m.start() for m in re.finditer('BRI_CAPI_B3_CLEARED endpoint=', region)]
        signal_positions = [m.start() for m in re.finditer('BRI_CAPI_SIGNAL_CLEARED endpoint=', region)]
        ordered = (bool(active_positions) and bool(data_positions) and bool(b3_positions) and bool(signal_positions)
            and max(active_positions) < connected < min(data_positions)
            and max(data_positions) < media < min(b3_positions)
            and max(b3_positions) < min(signal_positions) and max(signal_positions) < teardown < released)
        run['passed'] = (bool(expected) and payloads_ok and ordered
            and sorted(record['endpoint'] for record in received) == expected
            and all(sorted(run[key]) == expected for key in ('bearers', 'tx', 'b3_cleared', 'signal_cleared'))
            and len(run['baseline_entities']) == 4 and run['baseline_entities'] == run['release_entities']
            and bool(before) and len(after) == 1 and after[-1] > before[-1]
            and 'BRI_CAPI_CALLS_CONNECTED endpoints=' + str(len(expected)) in region
            and 'BRI_CAPI_MEDIA_OK endpoints=' + str(len(expected)) + ' bytes=2048 round=' + str(round_index) in region
            and 'BRI_CAPI_TEARDOWN_OK endpoints=' + str(len(expected)) in region
            and 'BRI_CAPI_RELEASE_OK round=' + str(round_index) in region
            and 'BRI_CAPI_CALLS_OK' in region and 'BRI_CAPI_CALL_FAILED' not in region)
        runs.append(run)
    count = runs[0]['calls'] if runs and all(r['calls'] == runs[0]['calls'] for r in runs) else 0
    passed = (bool(runs) and len(runs) <= 64 and capi_ok and all(r['passed'] for r in runs)
        and [r['round'] for r in runs] == list(range(1, len(runs) + 1))
        and board.get('sport_peers') == {str(i): i ^ 2 for i in range(8)})
    return dict(capi_call_count=count, capi_call_repeats=len(runs), capi_runs=runs, guest_calls=passed)


def verify(qemu, kernel, initrd, output, timeout=120, shared_ram=False, require_firmware=False, require_idi=False, require_capi=False, wire_pairs=False, require_calls=0, require_call_repeats=0, require_fpga=False, require_power_cycle=False):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    backend_path = output.with_suffix('.backend.log')
    serial_path = output.with_suffix('.serial.log')
    rpc_path = output.with_suffix('.rpc.jsonl')
    report = dict(guest_driver_attached=False, firmware_staged=False, shared_sdram=shared_ram,
                  kernel_sha256=hashlib.sha256(Path(kernel).read_bytes()).hexdigest(),
                  initrd_sha256=hashlib.sha256(Path(initrd).read_bytes()).hexdigest())
    summary_path = output.with_suffix('.board.json')
    summary_path.unlink(missing_ok=True)
    frontend = backend = None
    with tempfile.TemporaryDirectory(prefix='bri-guest-', dir='/private/tmp') as directory, \
            backend_path.open('w') as backend_log, \
            output.with_suffix('.qemu.log').open('w') as qemu_log:
        board_socket = Path(directory) / 'bri.sock'
        ram_file = Path(directory) / 'sdram.bin'
        try:
            backend_command = [sys.executable, str(ROOT / 'tools/eicon_bri_rpc.py'),
                '--socket', str(board_socket), '--trace', str(rpc_path),
                '--summary', str(summary_path), '--legacy']
            if wire_pairs:
                backend_command += ['--wire-pairs']
            if shared_ram:
                backend_command += ['--shared-ram', str(ram_file)]
            backend = subprocess.Popen(backend_command, cwd=ROOT,
                stdout=backend_log, stderr=subprocess.STDOUT)
            wait_socket(board_socket, backend)
            device = f'diva-bri,id=bri0,addr=4,socket={board_socket}'
            if shared_ram:
                device += f',ram-file={ram_file}'
            frontend = subprocess.Popen([str(qemu), '-machine', 'pc', '-accel', 'tcg',
                '-nodefaults', '-display', 'none', '-m', '256M', '-no-reboot',
                '-icount', 'shift=0,sleep=off', '-serial', f'file:{serial_path}',
                '-kernel', str(kernel), '-initrd', str(initrd),
                '-append', 'console=ttyS0,115200 rdinit=/bri-init bri_probe=1 panic=-1',
                '-device', device],
                stdout=subprocess.DEVNULL, stderr=qemu_log)
            deadline = time.monotonic() + timeout
            while frontend.poll() is None:
                text = backend_path.read_text()
                if '"error":' in text:
                    report['backend_error'] = text[-2048:]
                    break
                if backend.poll() is not None:
                    report['backend_exit'] = backend.returncode
                    break
                if time.monotonic() >= deadline:
                    report['timeout'] = True
                    break
                time.sleep(0.1)
            text = serial_path.read_text(errors='replace') if serial_path.exists() else ''
            if frontend.poll() is not None:
                backend.wait(timeout=10)
            board = json.loads(summary_path.read_text()) if summary_path.exists() else {}
            report.update(inspect_guest(text, board))
            report['frontend_exit'] = frontend.poll()
            if rpc_path.exists():
                report['rpc_count'], report['rpc_faults'] = 0, []
                with rpc_path.open() as stream:
                    for line in stream:
                        record = json.loads(line)
                        report['rpc_count'] += 1
                        if record['status']:
                            report['rpc_faults'].append(record)
                report['guest_driver_attached'] &= not report['rpc_faults']
            report['completed'] = (report['guest_driver_attached'] and frontend.poll() == 0
                                   and not report.get('timeout')
                                   and (not require_firmware or report['firmware_started'])
                                   and (not require_idi or report['guest_idi'])
                                   and (not require_capi or report['guest_capi'])
                                   and (not require_calls or (report['guest_calls']
                                        and report['capi_call_count'] == require_calls))
                                   and (not require_call_repeats or (report['guest_calls']
                                        and report['capi_call_repeats'] == require_call_repeats))
                                   and (not require_fpga or report['guest_fpga'])
                                   and (not require_power_cycle or report['guest_power_cycle']))
            return report
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--initrd', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--shared-ram', action='store_true')
    parser.add_argument('--require-firmware', action='store_true')
    parser.add_argument('--require-idi', action='store_true')
    parser.add_argument('--require-capi', action='store_true')
    parser.add_argument('--wire-pairs', action='store_true')
    parser.add_argument('--require-calls', type=int, choices=(1, 2, 3, 4), default=0)
    parser.add_argument('--require-call-repeats', type=int, choices=range(1, 65), default=0)
    parser.add_argument('--require-fpga', action='store_true')
    parser.add_argument('--require-power-cycle', action='store_true')
    args = parser.parse_args()
    result = verify(args.qemu, args.kernel, args.initrd, args.out, args.timeout, args.shared_ram, args.require_firmware, args.require_idi, args.require_capi, args.wire_pairs, args.require_calls, args.require_call_repeats, args.require_fpga, args.require_power_cycle)
    print(json.dumps(result))
    raise SystemExit(0 if result.get('completed') else 1)
