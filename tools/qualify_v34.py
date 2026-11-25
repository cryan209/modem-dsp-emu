#!/usr/bin/env python3
"""Opt-in localhost V.34/LAPM/PPP qualification with a persistent answerer.

Exit 0 only after every call verifies independent bytes in BOTH directions,
clean PPP teardown, and clean child exits. Does not qualify physical hardware,
TCP throughput, bit error rate, or hard real-time timing. TX underruns are
reported and fail only when an explicit budget is exceeded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import eicon_loopback as loopback
from v34_payload import MAX_BYTES, Receiver, Server, expected_hash, write_json

ROOT = Path(__file__).resolve().parent.parent
MODULATION = 'v34,0,19200,19200,19200,19200'


def endpoint_result(text: str, max_underruns: int | None = None, *,
                    require_nat_cleanup: bool = False) -> dict:
    """Fail closed on missing evidence, reconnects, tracebacks, or errors."""
    summaries = re.findall(r'\[ppp\] lcp=.*fcs-errors=(\d+);[^\n]*?(\d+) data-link resets', text)
    media = re.findall(r'\[media\] call totals: substituted (\d+) RX samples, dropped (\d+),[^\n]*?(\d+) underruns', text)
    counters = dict(zip(('rx_substituted', 'rx_dropped', 'tx_underruns'), map(int, media[-1]))) if media else None
    checks = dict(
        data_mode=bool(re.search(r'TrnProgress (?:0x[0-9a-f]+ -> )?0x00d0', text)),
        single_lapm_connection=text.count('[v42] LAPM connected') == 1,
        authenticated=text.count("[ppp] authenticated 'ppp'") == 1,
        single_ipcp_up=text.count('[ppp] IPCP up') == 1,
        ppp_down=text.count('[ppp] IPCP down') == 1 and text.count('[ppp] LCP down') == 1,
        zero_fcs_errors_and_resets=len(summaries) == 1 and summaries[0] == ('0', '0'),
        final_media_counters=counters is not None,
        zero_rx_loss=counters is not None and counters['rx_substituted'] == counters['rx_dropped'] == 0,
        no_traceback='Traceback (most recent call last)' not in text,
    )
    if require_nat_cleanup:
        checks['nat_flow_closed'] = len(re.findall(r'\[ppp\] closed 1 flow\(s\) for ', text)) == 1
    if max_underruns is not None:
        checks['tx_underrun_budget'] = counters is not None and counters['tx_underruns'] <= max_underruns
    return dict(passed=all(checks.values()), checks=checks, media=counters)


def payload_result(token: bytes, size: int, client: dict, server: dict) -> dict:
    checks = dict(
        client_complete=client.get('complete') is True and not client.get('errors'),
        server_complete=server.get('complete') is True and not server.get('errors'),
        session_matches=client.get('token') == token.hex(),
        exact_upstream_bytes=server.get('bytes') == size,
        exact_downstream_bytes=client.get('bytes') == size,
        upstream_sha256=server.get('sha256') == expected_hash(token, b'up', size),
        downstream_sha256=client.get('sha256') == expected_hash(token, b'down', size),
    )
    return dict(passed=all(checks.values()), checks=checks, client=client, server=server)


def stop_process(process: subprocess.Popen, timeout: float = 15) -> dict:
    forced = False
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        forced = True
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    return dict(pid=process.pid, returncode=process.returncode, forced_kill=forced,
                passed=process.returncode == 0 and not forced)


def wait_for(predicate, processes, seconds: float, description: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f'endpoint {process.pid} exited early: {process.returncode}')
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError(f'timeout waiting for {description} ({seconds:g}s)')


def load_report(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--python', type=Path, default=Path(sys.executable))
    ap.add_argument('--output', type=Path, help='new evidence directory; never overwritten')
    ap.add_argument('--calls', type=int, default=3)
    ap.add_argument('--bytes', type=int, default=4096, help='exact verified bytes per direction, per call')
    ap.add_argument('--call-timeout', type=float, default=120, help='wall seconds including training')
    ap.add_argument('--max-tx-underruns', type=int, help='optional final per-end, per-call budget; 0 is strict')
    args = ap.parse_args()
    if not 1 <= args.calls <= 100 or not 1 <= args.bytes <= MAX_BYTES:
        ap.error(f'--calls must be 1..100; --bytes must be 1..{MAX_BYTES}')
    if not math.isfinite(args.call_timeout) or not 10 <= args.call_timeout <= 3600:
        ap.error('--call-timeout must be finite and 10..3600 seconds')
    if args.max_tx_underruns is not None and args.max_tx_underruns < 0:
        ap.error('--max-tx-underruns must be nonnegative')
    # Do not resolve the venv's interpreter symlink into the global Python.
    args.python = Path(os.path.abspath(args.python))
    if not args.python.is_file():
        ap.error(f'interpreter does not exist: {args.python}')
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        args.output = ROOT / 'artifacts/qualification' / f'v34-{stamp}-{secrets.token_hex(3)}'
    args.output = args.output.resolve()
    if args.output.exists():
        ap.error('--output must not already exist')
    return args


def record_inputs(args, env):
    """Fingerprint actual extracted firmware, native library and source inputs."""
    script = """
import importlib.metadata, json, sys
sys.path.insert(0, 'tools')
from dial_tikrnl_drive import FIRMWARE_SETS
print(json.dumps(dict(python_version=sys.version,
    packages={name: importlib.metadata.version(name) for name in ('unicorn', 'numpy')},
    firmware={name: {key: FIRMWARE_SETS[name][key] for key in ('kernel', 'tikrnl', 'overlays')}
              for name in ('analog109', 'pri117')})))
"""
    data = subprocess.run([str(args.python), '-c', script], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=True, timeout=30)
    inputs = json.loads(data.stdout)
    paths = set()
    for firmware in inputs['firmware'].values():
        for relative in firmware.values():
            path = ROOT / relative
            if not path.exists():
                raise FileNotFoundError(f'missing extracted firmware: {relative}; see docs/testing.md')
            paths.update(path.rglob('*') if path.is_dir() else [path])
    paths.update(ROOT / 'tools' / name for name in (
        'qualify_v34.py', 'v34_payload.py', 'eicon_loopback.py', 'eicon_adsp_sip.py',
        'analog_kernel_dispatch.py', 'dial_tikrnl_drive.py',
        'adsp2181emu/libadsp2181.dylib'))
    inputs['sha256'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(paths) if path.is_file()}
    # Source archives are valid inputs too: hashes remain authoritative when
    # Git is absent or this directory has no .git metadata.
    inputs['git_revision'] = None
    try:
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                  capture_output=True, text=True, timeout=5)
        if revision.returncode == 0:
            inputs['git_revision'] = revision.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    write_json(args.output / 'inputs.json', inputs)


def run(args, report):
    output = args.output
    # Remove diagnostic tuning inherited from developer shells. Record the
    # removed keys, never their possibly sensitive values.
    removed = sorted(k for k in os.environ if k.startswith(('EICON_', 'ADSP_')))
    env = {k: v for k, v in os.environ.items() if k not in removed}
    report['removed_environment_keys'] = removed
    with (output / 'preflight.log').open('w') as log:
        subprocess.run([str(args.python), str(ROOT / 'tools/check_test_environment.py')],
                       cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=30)
    record_inputs(args, env)
    settings = loopback.argument_parser().parse_args([
        '--python', str(args.python), '--ppp', '--caller-kernel-dispatch',
        '--caller-firmware-set', 'analog109', '--answerer-firmware-set', 'pri117',
        '--modulation', MODULATION, '--trace-retrain'])
    answer_sip = loopback.free_port(25070)
    caller_sip = loopback.free_port(answer_sip + 2)
    answer_rtp = loopback.free_port(24010)
    caller_rtp = loopback.free_port(answer_rtp + 2)
    report['ports'] = dict(answerer_sip=answer_sip, caller_sip=caller_sip,
                           answerer_rtp=answer_rtp, caller_rtp=caller_rtp)
    answer_command = loopback.build_command(
        settings, role='answer', firmware_set='pri117', native_mips=False,
        sip_port=answer_sip, rtp_port=answer_rtp, prefix=output / 'answerer',
        dial=None, modulation=MODULATION)
    answer_env = loopback.end_environment(env, MODULATION, 'pri117', 'answerer')
    caller_env = loopback.end_environment(env, MODULATION, 'analog109', 'caller')
    answer_log = output / 'answerer.endpoint.log'
    report['answerer_command'] = answer_command
    with answer_log.open('w') as answer_handle:
        answerer = subprocess.Popen(answer_command, cwd=ROOT, env=answer_env,
                                    stdout=answer_handle, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        report['answerer_pid'] = answerer.pid
        try:
            wait_for(lambda: '[sip] listening on ' in answer_log.read_text(),
                     [answerer], 20, 'answerer readiness')
            for number in range(1, args.calls + 1):
                call_dir = output / f'call-{number:03}'
                call_dir.mkdir()
                token = secrets.token_bytes(16)
                receiver = Receiver(token, args.bytes)
                cycle = dict(number=number, answerer_pid=answerer.pid, passed=False)
                report['calls'].append(cycle)
                offset = len(answer_log.read_text())
                client_path = call_dir / 'client.json'
                caller_log = call_dir / 'caller.endpoint.log'
                try:
                    with Server(receiver) as server:
                        config = call_dir / 'probe.json'
                        write_json(config, dict(token=token.hex(), bytes=args.bytes,
                                                port=server.port, report=str(client_path)))
                        command = loopback.build_command(
                            settings, role='calling', firmware_set='analog109',
                            native_mips=False, kernel_dispatch=True,
                            sip_port=caller_sip, rtp_port=caller_rtp,
                            prefix=call_dir / 'caller', dial=('6001', answer_sip),
                            modulation=MODULATION) + ['--ppp-qualify', str(config)]
                        cycle['command'] = command
                        with caller_log.open('w') as handle:
                            caller = subprocess.Popen(command, cwd=ROOT, env=caller_env,
                                                      stdout=handle, stderr=subprocess.STDOUT,
                                                      start_new_session=True)
                            try:
                                wait_for(lambda: client_path.exists(), [answerer, caller],
                                         args.call_timeout, f'call {number} exact payload')
                            finally:
                                cycle['caller_process'] = stop_process(caller)
                        # Reuse the SAME answerer, testing PPP pool/NAT/call
                        # cleanup rather than process startup three times.
                        wait_for(lambda: '[call] teardown complete' in answer_log.read_text()[offset:],
                                 [answerer], 15, f'call {number} remote teardown')
                finally:
                    server_result = receiver.result()
                    write_json(call_dir / 'server.json', server_result)
                    client_result = load_report(client_path)
                    answer_text = answer_log.read_text()[offset:]
                    (call_dir / 'answerer.endpoint.log').write_text(answer_text)
                    cycle['payload'] = payload_result(token, args.bytes, client_result, server_result)
                    caller_text = caller_log.read_text() if caller_log.exists() else ''
                    cycle['caller'] = endpoint_result(caller_text, args.max_tx_underruns)
                    cycle['answerer'] = endpoint_result(answer_text, args.max_tx_underruns,
                                                       require_nat_cleanup=True)
                    cycle['remote_teardown'] = ('[call] ended after ' in answer_text
                                                and '[call] teardown complete' in answer_text)
                    cycle['passed'] = (cycle['payload']['passed'] and cycle['caller']['passed']
                                       and cycle['answerer']['passed'] and cycle['remote_teardown']
                                       and cycle.get('caller_process', {}).get('passed') is True)
                    write_json(call_dir / 'result.json', cycle)
                print(f'Call {number}/{args.calls}: {"PASS" if cycle["passed"] else "FAIL"}', flush=True)
                write_json(output / 'result.json', report)
                if not cycle['passed']:
                    break
        finally:
            report['answerer_process'] = stop_process(answerer)


def main():
    args = parser()
    args.output.mkdir(parents=True)
    report = dict(schema_version=1, passed=False,
                  scope='local V.34 19200 LAPM/PPP; persistent PRI answerer, fresh Analog callers; stop-and-wait UDP, not TCP throughput or physical hardware',
                  platform=platform.platform(), python=str(args.python), modulation=MODULATION,
                  requested_calls=args.calls, bytes_per_direction=args.bytes,
                  call_timeout_seconds=args.call_timeout,
                  max_tx_underruns=args.max_tx_underruns, calls=[], errors=[])
    code = 1
    try:
        run(args, report)
        report['passed'] = (len(report['calls']) == args.calls
                            and all(call['passed'] for call in report['calls'])
                            and report.get('answerer_process', {}).get('passed') is True)
        code = 0 if report['passed'] else 1
    except KeyboardInterrupt:
        report['errors'].append('interrupted')
        code = 130
    except Exception as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
    finally:
        write_json(args.output / 'result.json', report)
    print(f'{"PASS" if report["passed"] else "FAIL"}: {args.output / "result.json"}')
    for error in report['errors']:
        print(error, file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
