#!/usr/bin/env python3
"""Opt-in localhost V.34/LAPM/PPP qualification with a persistent answerer.

Exit 0 only after every call verifies independent bytes in BOTH directions,
clean PPP teardown, clean child exits and complete source-side RTP timing captures. Does not qualify physical hardware,
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
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import eicon_loopback as loopback
from v34_payload import BLOCK, MAX_BYTES, Receiver, Server, expected_hash, write_json

ROOT = Path(__file__).resolve().parent.parent
MODULATION = 'v34,0,19200,19200,19200,19200'


def endpoint_result(text: str, max_underruns: int | None = None, *,
                    require_nat_cleanup: bool = False) -> dict:
    """Fail closed on missing evidence, reconnects, tracebacks, or errors."""
    summaries = re.findall(r'\[ppp\] lcp=.*fcs-errors=(\d+);[^\n]*?(\d+) data-link resets', text)
    media = re.findall(r'\[media\] call totals: substituted (\d+) RX samples, dropped (\d+),[^\n]*?(\d+) underruns[^\n]*?sent (\d+)', text)
    counters = dict(zip(('rx_substituted', 'rx_dropped', 'tx_underruns', 'tx_sends'), map(int, media[-1]))) if media else None
    transitions = re.findall(r'\[adsp\][^\n]*?\bTrnProgress 0x([0-9a-f]{4}) -> 0x([0-9a-f]{4})\b', text)
    training = dict(data_mode_entries=sum(new == '00d0' for old, new in transitions),
                    data_mode_departures=sum(old == '00d0' and new != '00d0' for old, new in transitions))
    checks = dict(
        data_mode=bool(re.search(r'TrnProgress (?:0x[0-9a-f]+ -> )?0x00d0', text)),
        single_data_mode_entry=training['data_mode_entries'] == 1,
        no_logged_data_mode_departure=training['data_mode_departures'] == 0,
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
    return dict(passed=all(checks.values()), checks=checks, media=counters, training=training)


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


def wait_for(predicate, processes, seconds: float, description: str, *, on_poll=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f'endpoint {process.pid} exited early: {process.returncode}')
        if predicate():
            return
        if on_poll is not None:
            on_poll()
        time.sleep(0.05)
    raise TimeoutError(f'timeout waiting for {description} ({seconds:g}s)')


def load_report(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--python', type=Path, default=Path(sys.executable))
    ap.add_argument('--output', type=Path, help='new evidence directory; never overwritten')
    ap.add_argument('--media-trace', action='store_true', help='dump bounded per-call media event traces at teardown')
    ap.add_argument('--producer-detail', action='store_true',
                    help='bounded GC/emulated-cycle observations; implies --producer-profile')
    ap.add_argument('--producer-profile', action='store_true',
                    help='bounded producer CPU/wall phase measurements; implies --media-trace')
    ap.add_argument('--calls', type=int, default=3)
    ap.add_argument('--bytes', type=int, default=4096, help='exact verified bytes per direction, per call')
    ap.add_argument('--call-timeout', type=float, default=120, help='wall seconds including training')
    ap.add_argument('--max-tx-underruns', type=int, help='optional final per-end, per-call budget; 0 is strict')
    ap.add_argument('--max-tx-clock-ppm', type=float, default=1000,
                    help='maximum absolute source TX clock offset; default 1000 ppm')
    ap.add_argument('--max-tx-gap-ms', type=float,
                    help='optional source-side maximum inter-packet spacing budget')
    ap.add_argument('--caller-pause-ms', type=float, default=0,
                    help='opt-in whole-caller-process SIGSTOP during data, 1..1000 ms; default disabled')
    ap.add_argument('--producer-pause-ms', type=float, default=0,
                    help='opt-in producer/RX-dispatch pause with wire thread running; 1..1000 ms')
    ap.add_argument('--pause-after-bytes', type=int, default=1024,
                    help='verified upstream threshold for each pause; multiple of 64, leaves >=128 bytes')
    args = ap.parse_args()
    if args.producer_detail:
        args.producer_profile = True
    if args.producer_profile:
        args.media_trace = True
    if not 1 <= args.calls <= 100 or not 1 <= args.bytes <= MAX_BYTES:
        ap.error(f'--calls must be 1..100; --bytes must be 1..{MAX_BYTES}')
    if not math.isfinite(args.call_timeout) or not 10 <= args.call_timeout <= 3600:
        ap.error('--call-timeout must be finite and 10..3600 seconds')
    if args.max_tx_underruns is not None and args.max_tx_underruns < 0:
        ap.error('--max-tx-underruns must be nonnegative')
    if not math.isfinite(args.max_tx_clock_ppm) or not 0 <= args.max_tx_clock_ppm < 1_000_000:
        ap.error('--max-tx-clock-ppm must be finite and 0 <= value < 1000000')
    if args.max_tx_gap_ms is not None and (not math.isfinite(args.max_tx_gap_ms) or args.max_tx_gap_ms <= 0):
        ap.error('--max-tx-gap-ms must be positive and finite')
    if not math.isfinite(args.caller_pause_ms) or not (args.caller_pause_ms == 0 or 1 <= args.caller_pause_ms <= 1000):
        ap.error('--caller-pause-ms must be 0 (disabled) or finite and 1..1000')
    if not math.isfinite(args.producer_pause_ms) or not (args.producer_pause_ms == 0 or 1 <= args.producer_pause_ms <= 1000):
        ap.error('--producer-pause-ms must be 0 (disabled) or finite and 1..1000')
    if args.caller_pause_ms and args.producer_pause_ms:
        ap.error('whole-caller and producer pauses are mutually exclusive')
    if args.caller_pause_ms:
        if not all(hasattr(signal, name) for name in ('SIGSTOP', 'SIGCONT')) or not hasattr(os, 'WUNTRACED'):
            ap.error('caller pause requires POSIX SIGSTOP/SIGCONT and waitpid stop observation')
    if args.producer_pause_ms and os.name != 'posix':
        ap.error('producer pause requires POSIX inherited pipe descriptors')
    if args.caller_pause_ms or args.producer_pause_ms:
        if not BLOCK <= args.pause_after_bytes <= args.bytes - 2 * BLOCK or args.pause_after_bytes % BLOCK:
            ap.error('--pause-after-bytes must be a positive multiple of 64 leaving at least 128 bytes')
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
        'qualify_v34.py', 'v34_payload.py', 'media_clock_trace.py', 'rtp_qualification.py',
        'qualification_faults.py', 'producer_pause.py', 'producer_profile.py', 'producer_detail.py',
        'eicon_loopback.py', 'eicon_adsp_sip.py',
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
    if args.media_trace:
        answer_command += ['--media-trace-dir', str(output / 'answerer-media-traces')]
    if args.producer_profile:
        answer_command += ['--producer-profile']
    if args.producer_detail:
        answer_command += ['--producer-detail']
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
                cycle = dict(number=number, answerer_pid=answerer.pid, passed=False,
                             protocol_passed=False, source_timing_pending=True)
                if args.caller_pause_ms:
                    cycle['caller_pause'] = dict(requested_ms=args.caller_pause_ms,
                                               trigger_after_bytes=args.pause_after_bytes,
                                               attempted=False)
                if args.producer_pause_ms:
                    cycle['producer_pause'] = dict(requested_ms=args.producer_pause_ms,
                                                  trigger_after_bytes=args.pause_after_bytes,
                                                  attempted=False)
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
                        if args.media_trace:
                            command += ['--media-trace-dir', str(call_dir / 'caller-media-traces')]
                        if args.producer_profile:
                            command += ['--producer-profile']
                        if args.producer_detail:
                            command += ['--producer-detail']
                        cycle['command'] = command
                        with caller_log.open('w') as handle, ExitStack() as resources:
                            inherited = ()
                            if args.producer_pause_ms:
                                read_fd, write_fd = os.pipe()
                                reader = resources.enter_context(os.fdopen(read_fd, 'rb', buffering=0))
                                writer = resources.enter_context(os.fdopen(write_fd, 'wb', buffering=0))
                                pause_config = call_dir / 'producer-pause.config.json'
                                write_json(pause_config, dict(read_fd=reader.fileno(),
                                           milliseconds=args.producer_pause_ms,
                                           receipt=str(call_dir / 'producer-pause.endpoint.json')))
                                command += ['--qualification-producer-pause', str(pause_config)]
                                inherited = (reader.fileno(),)
                            caller = subprocess.Popen(command, cwd=ROOT, env=caller_env,
                                                      stdout=handle, stderr=subprocess.STDOUT,
                                                      start_new_session=True, pass_fds=inherited)
                            if inherited:
                                reader.close()  # only the caller retains the read end

                            def maybe_pause():
                                if args.producer_pause_ms:
                                    from producer_pause import request_on_progress
                                    request_on_progress(writer, receiver, cycle['producer_pause'],
                                                        call_dir / 'producer-pause.endpoint.json',
                                                        call_dir / 'producer-pause.json')
                                else:
                                    from qualification_faults import pause_on_progress
                                    pause_on_progress(caller, receiver, cycle['caller_pause'],
                                                      call_dir / 'caller-pause.json')

                            try:
                                wait_for(lambda: client_path.exists(), [answerer, caller],
                                         args.call_timeout, f'call {number} exact payload',
                                         on_poll=maybe_pause if args.caller_pause_ms or args.producer_pause_ms else None)
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
                    if args.media_trace:
                        traces = dict(caller=call_dir / 'caller-media-traces/call-001.json',
                                      answerer=output / f'answerer-media-traces/call-{number:03}.json')
                        cycle['media_traces'] = {role: str(path) for role, path in traces.items()}
                        from media_clock_trace import valid_receipt
                        cycle['trace_complete'] = all(
                            valid_receipt(load_report(path), (cycle[role]['media'] or {}).get('tx_underruns'))
                            for role, path in traces.items())
                    if args.producer_profile:
                        from producer_profile import valid_receipt as valid_profile
                        profiles = dict(caller=call_dir / 'caller-media-traces/producer-profiles/call-001.json',
                                        answerer=output / f'answerer-media-traces/producer-profiles/call-{number:03}.json')
                        cycle['producer_profiles'] = {role: str(path) for role, path in profiles.items()}
                        cycle['profile_complete'] = all(valid_profile(load_report(path)) for path in profiles.values())
                        if args.producer_detail:
                            from producer_detail import valid_receipt as valid_detail
                            receipts = [load_report(path) for path in profiles.values()]
                            cycle['detail_complete'] = all(valid_detail(receipt.get('detail', {}),
                                receipt.get('completed_quanta')) for receipt in receipts)
                    cycle['remote_teardown'] = ('[call] ended after ' in answer_text
                                                and '[call] teardown complete' in answer_text)
                    if args.caller_pause_ms:
                        from qualification_faults import pause_result
                        pause_result(cycle['caller_pause'], server_result['bytes'])
                        write_json(call_dir / 'caller-pause.json', cycle['caller_pause'])
                    if args.producer_pause_ms:
                        from producer_pause import application_result
                        application_result(cycle['producer_pause'],
                                           load_report(call_dir / 'producer-pause.endpoint.json'), server_result['bytes'])
                        write_json(call_dir / 'producer-pause.json', cycle['producer_pause'])
                    cycle['protocol_passed'] = (cycle['payload']['passed'] and cycle['caller']['passed']
                                       and cycle['answerer']['passed'] and cycle['remote_teardown']
                                       and cycle.get('caller_process', {}).get('passed') is True
                                       and cycle.get('trace_complete', True)
                                       and cycle.get('profile_complete', True)
                                       and cycle.get('detail_complete', True)
                                       and cycle.get('caller_pause', {}).get('passed', not args.caller_pause_ms)
                                       and cycle.get('producer_pause', {}).get('application_passed', not args.producer_pause_ms))
                    write_json(call_dir / 'result.json', cycle)
                print(f'Protocol call {number}/{args.calls}: {"PASS" if cycle["protocol_passed"] else "FAIL"} (source timing pending)', flush=True)
                write_json(output / 'result.json', report)
                if not cycle['protocol_passed']:
                    break
        finally:
            report['answerer_process'] = stop_process(answerer)


def qualify_source_timing(args, report):
    """Analyze closed captures only, using each emitter's own TX timestamps."""
    from rtp_qualification import evaluate_pair, read_capture
    timing = dict(passed=False, errors=[], checks={})
    report['source_timing'] = timing
    answerer = []
    try:
        answerer = read_capture(args.output / 'answerer.rtp.pcap')
    except (OSError, ValueError) as exc:
        timing['errors'].append(f'answerer capture: {exc}')
    ports = report['ports']
    matched = []
    for cycle in report['calls']:
        directory = args.output / f'call-{cycle["number"]:03}'
        try:
            caller = read_capture(directory / 'caller.rtp.pcap')
            result = evaluate_pair(caller, answerer, ports['caller_rtp'], ports['answerer_rtp'],
                                   (cycle['caller']['media'] or {}).get('tx_sends'),
                                   (cycle['answerer']['media'] or {}).get('tx_sends'),
                                   args.max_tx_clock_ppm, args.max_tx_gap_ms)
            if 'producer_pause' in cycle:
                from rtp_qualification import source_window
                fault = cycle['producer_pause'].get('endpoint', {})
                source = result.get('caller')
                window = (source_window(directory / 'caller.rtp.pcap', source,
                                        fault.get('begin_wall_ns'), fault.get('end_wall_ns'))
                          if source and source['ssrc'] == fault.get('ssrc') else dict(passed=False, packets=0))
                result['producer_pause_wire_window'] = window
                result['checks']['producer_pause_wire_evidence'] = window['passed']
                result['passed'] = result['passed'] and window['passed']
        except (OSError, ValueError) as exc:
            result = dict(passed=False, errors=[str(exc)])
        cycle['source_timing'] = result
        cycle['source_timing_pending'] = False
        cycle['passed'] = cycle['protocol_passed'] and result['passed']
        if 'answerer' in result:
            matched.append(result['answerer']['ssrc'])
    timing['checks']['all_answerer_streams_accounted_for'] = (
        len(matched) == len(set(matched)) == len(report['calls'])
        and sum(stream.sport == ports['answerer_rtp'] for stream in answerer) == len(matched))
    timing['passed'] = (bool(report['calls']) and not timing['errors']
                        and all(timing['checks'].values())
                        and all(c['source_timing']['passed'] for c in report['calls']))
    for cycle in report['calls']:
        cycle['source_capture_accounting'] = all(timing['checks'].values()) and not timing['errors']
        cycle['passed'] = cycle['passed'] and cycle['source_capture_accounting']
        write_json(args.output / f'call-{cycle["number"]:03}' / 'result.json', cycle)
        print(f'Call {cycle["number"]} including source timing: {"PASS" if cycle["passed"] else "FAIL"}', flush=True)
    timing['calls'] = {str(c['number']): c['source_timing'] for c in report['calls']}
    write_json(args.output / 'source-timing.json', timing)


def main():
    args = parser()
    args.output.mkdir(parents=True)
    report = dict(schema_version=2, passed=False,
                  scope='local V.34 19200 LAPM/PPP; persistent PRI answerer, fresh Analog callers; stop-and-wait UDP, not TCP throughput or physical hardware',
                  platform=platform.platform(), python=str(args.python), modulation=MODULATION,
                  requested_calls=args.calls, bytes_per_direction=args.bytes,
                  call_timeout_seconds=args.call_timeout,
                  max_tx_underruns=args.max_tx_underruns,
                  max_tx_clock_ppm=args.max_tx_clock_ppm, max_tx_gap_ms=args.max_tx_gap_ms,
                  media_trace=args.media_trace, producer_profile=args.producer_profile,
                  producer_detail=args.producer_detail,
                  caller_pause_ms=args.caller_pause_ms,
                  producer_pause_ms=args.producer_pause_ms,
                  pause_after_bytes=args.pause_after_bytes if args.caller_pause_ms or args.producer_pause_ms else None,
                  calls=[], errors=[])
    code = 1
    try:
        run(args, report)
        qualify_source_timing(args, report)
        report['passed'] = (report['source_timing']['passed'] and len(report['calls']) == args.calls
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
