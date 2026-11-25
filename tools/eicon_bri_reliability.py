#!/usr/bin/env python3
"""Run bounded real-line duplex transfers against the BRI SIP emulator.

Stages only the standard-library integrity probe on the specified card host.
Keeps one emulator registration across reconnects, arms the PTY before every
dial, and stops at the first failure. No persistent host/router changes.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time

PROBE = Path(__file__).with_name('eicon_stream_integrity.py').resolve()


def events(path):
    result = []
    for line in path.read_text(errors='replace').splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result


def stop(process):
    if process and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def transfer(args, endpoint, tty_name, name, size, duration=0):
    local = remote = None
    root = args.out / name
    root.mkdir()
    local_log = root / 'pty.jsonl'
    remote_log = root / 'serial.jsonl'
    result_path = root / 'pty-result.json'
    started = time.monotonic()
    acknowledged = False
    stream_started = False
    sent_limits = set()
    try:
        with local_log.open('w') as ll, remote_log.open('w') as rl:
            local = subprocess.Popen([sys.executable, '-u', str(PROBE),
                '--mode', 'pty', '--device', tty_name, '--size', str(size),
                '--seed', name, '--timeout', str(args.transfer_timeout + 90),
                '--duration', str(duration), '--result', str(result_path)],
                stdin=subprocess.PIPE, stdout=ll, stderr=subprocess.STDOUT, text=True)
            for _ in range(50):
                if any(e.get('event') == 'armed' for e in events(local_log)):
                    break
                if local.poll() is not None:
                    raise RuntimeError('PTY verifier failed before arming')
                time.sleep(.1)
            else:
                raise RuntimeError('PTY verifier did not arm')
            command = ['sudo', '-n', 'timeout', str(args.transfer_timeout + 110),
                       'python3', args.remote_probe, '--mode', 'serial',
                       '--device', args.tty, '--controller', str(args.controller),
                       '--modem', args.modem,
                       '--number', args.username, '--size', str(size), '--seed', name,
                       '--timeout', str(args.transfer_timeout), '--duration', str(duration)]
            physical_command = (command if args.local_host else
                ['ssh', '-o', 'BatchMode=yes', args.host, shlex.join(command)])
            remote = subprocess.Popen(physical_command, stdin=subprocess.PIPE,
                                      stdout=rl, stderr=subprocess.STDOUT, text=True)
            print(json.dumps(dict(event='phase_started', phase=name,
                                  bytes_each_way=None if duration else size,
                                  duration_seconds=duration)), flush=True)
            last_update = time.monotonic()
            while time.monotonic() - started < args.transfer_timeout + 120:
                if endpoint.poll() is not None:
                    raise RuntimeError('SIP emulator exited')
                local_result = json.loads(result_path.read_text()) if result_path.exists() else None
                if local_result and not local_result['passed']:
                    raise RuntimeError(f'PTY verifier failed: {local_result}')
                rows = events(remote_log)
                if duration:
                    if not stream_started and any(r.get('event') == 'connected' for r in rows):
                        local.stdin.write('START\n')
                        local.stdin.flush()
                        stream_started = True
                    for source, destination, key in ((rows, local, 'remote'),
                            (events(local_log), remote, 'local')):
                        final_tx = next((r['tx'] for r in source if r.get('event') == 'tx_final'), None)
                        if final_tx is not None and key not in sent_limits:
                            destination.stdin.write(f'RX_LIMIT {final_tx}\n')
                            destination.stdin.flush()
                            sent_limits.add(key)
                failures = [r for r in rows if r.get('event') == 'result' and not r.get('passed')]
                if failures:
                    raise RuntimeError(f'physical verifier failed: {failures[-1]}')
                if local_result and any(r.get('event') == 'data_pass' for r in rows) and not acknowledged:
                    remote.stdin.write('ACK\n')
                    remote.stdin.flush()
                    acknowledged = True
                if remote.poll() is not None:
                    final = next((r for r in reversed(rows) if r.get('event') == 'result'), None)
                    if remote.returncode or not acknowledged or not final or not final['passed']:
                        raise RuntimeError(f'physical test ended without a verified pass ({remote.returncode})')
                    local.wait(timeout=5)
                    result = dict(passed=True, phase=name,
                                  bytes_each_way=None if duration else size,
                                  duration_seconds=duration,
                                  pty=local_result, serial=final,
                                  seconds=time.monotonic() - started)
                    (root / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
                    print(json.dumps(result), flush=True)
                    return result
                if time.monotonic() - last_update >= 30:
                    latest = next((r for r in reversed(rows) if r.get('event') in ('progress', 'connected')), {})
                    print(json.dumps(dict(event='phase_progress', phase=name, serial=latest)), flush=True)
                    last_update = time.monotonic()
                time.sleep(.2)
            raise TimeoutError('phase deadline exceeded')
    finally:
        if remote and remote.poll() is None:
            try:
                remote.stdin.write('CANCEL\n')
                remote.stdin.flush()
                remote.wait(timeout=10)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                stop(remote)
        stop(local)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--local-host', action='store_true',
                        help='run the physical TTY probe on this host without SSH')
    parser.add_argument('--registrar', required=True)
    parser.add_argument('--username', default='6001')
    parser.add_argument('--password', default=os.environ.get('EICON_LAB_PASSWORD', ''))
    parser.add_argument('--tty', default='/dev/ttyds6')
    parser.add_argument('--controller', type=int, default=2)
    parser.add_argument('--modem', choices=('eicon', 'multitech'), default='eicon')
    parser.add_argument('--large-bytes', type=int, default=1048576)
    parser.add_argument('--calls', type=int, default=20)
    parser.add_argument('--call-bytes', type=int, default=8192)
    parser.add_argument('--transfer-timeout', type=int, default=900)
    parser.add_argument('--soak-seconds', type=int, default=0)
    parser.add_argument('--capture', action='store_true',
                        help='capture RTP, codec audio and per-frame DSP state')
    parser.add_argument('--rx-jitter-ms', type=int, default=200)
    parser.add_argument('--tx-buffer-ms', type=int, default=160)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.password or min(args.large_bytes, args.calls, args.call_bytes) < 0 or args.transfer_timeout <= 0:
        parser.error('password required; sizes/call count must be nonnegative; timeout positive')
    if args.soak_seconds < 0 or (not args.large_bytes and not args.calls and not args.soak_seconds) or (args.calls and not args.call_bytes):
        parser.error('at least one nonempty transfer is required')
    args.out.mkdir(parents=True, exist_ok=False)
    args.remote_probe = str(PROBE) if args.local_host else (
        '/var/tmp/eicon-stream-integrity-' + str(os.getpid()) + '.py')
    if not args.local_host:
        subprocess.run(['scp', '-q', str(PROBE), args.host + ':' + args.remote_probe], check=True)
    endpoint = None
    result = dict(passed=False, phases=[])
    try:
        log_path = args.out / 'endpoint.log'
        command = [sys.executable, '-u', 'tools/eicon_adsp_sip.py',
                   '--bri-kernel-dispatch', '--tx-v42', '--v42-pty', '--law', 'pcmu',
                   '--sip-port', '15060', '--rtp-port', '14000',
                   '--rx-jitter-ms', str(args.rx_jitter_ms),
                   '--rx-depth-ms', '2000', '--rx-hold-ms', '1000',
                   '--tx-buffer-ms', str(args.tx_buffer_ms),
                   '--registrar', args.registrar, '--username', args.username,
                   '--password', args.password]
        if args.capture:
            command += ['--capture-prefix', str(args.out / 'media')]
        with log_path.open('w') as log:
            endpoint = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        for _ in range(150):
            text = log_path.read_text()
            match = re.search(r'terminal ready on (\S+)', text)
            if match and '[sip] registered' in text:
                break
            if endpoint.poll() is not None:
                raise RuntimeError('endpoint failed to start')
            time.sleep(.1)
        else:
            raise RuntimeError('endpoint failed to register')
        phases = ([('large', args.large_bytes)] if args.large_bytes else [])
        phases += [(f'reconnect-{i + 1:02d}', args.call_bytes) for i in range(args.calls)]
        for name, size in phases:
            result['phases'].append(transfer(args, endpoint, match[1], name, size))
            # Allow the peer DISC/BYE and the endpoint's call cleanup to drain.
            time.sleep(3)
        if args.soak_seconds:
            args.transfer_timeout = args.soak_seconds + 180
            result['phases'].append(transfer(args, endpoint, match[1], 'soak',
                                            2**40, args.soak_seconds))
        result['passed'] = True
    except KeyboardInterrupt:
        result['error'] = 'diagnostic run interrupted; incomplete phases are not passes'
        print(json.dumps(dict(event='interrupted', error=result['error'])), flush=True)
    except Exception as error:
        result['error'] = str(error)
        print(json.dumps(dict(event='failure', error=str(error))), flush=True)
    finally:
        stop(endpoint)
        (args.out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
