"""Opt-in faults against an owned localhost qualification child, never hardware.

SIGSTOP freezes the WHOLE process (producer, wire sender and receive dispatch),
not just DSP work. Resume before propagating exceptions, including SIGINT and
SIGTERM. SIGKILL/controller crashes cannot be made cleanup-safe by Python.
"""
import os
import signal
import time


def pause_owned_process(process, milliseconds, receipt):
    """Observe an owned child's stop, hold it, then request continuation.

    waitpid's stopped status does not reap the child. If it exits instead,
    preserve its real status in Popen so later cleanup does not invent exit 0.
    Only the qualifier's newly created caller Popen is supplied by the CLI.
    """
    if not 1 <= milliseconds <= 1000:
        raise ValueError('pause duration must be 1..1000 ms')
    if process.poll() is not None:
        raise RuntimeError('caller exited before fault injection')

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        receipt.update(pid=process.pid, stop_requested_wall_ns=time.time_ns(),
                       stop_requested_monotonic_ns=time.monotonic_ns())
        # The finally is active BEFORE sending STOP, so an interrupt between
        # publication of the signal and receipt bookkeeping still resumes.
        try:
            process.send_signal(signal.SIGSTOP)
            deadline = time.monotonic() + .5
            while time.monotonic() < deadline:
                pid, status = os.waitpid(process.pid, os.WUNTRACED | os.WNOHANG)
                if pid:
                    if os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGSTOP:
                        receipt.update(stop_observed=True,
                                       stop_observed_wall_ns=time.time_ns(),
                                       stop_observed_monotonic_ns=time.monotonic_ns())
                        break
                    if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                        process.returncode = os.waitstatus_to_exitcode(status)
                        raise RuntimeError(f'caller exited during injection: {process.returncode}')
                    raise RuntimeError(f'unexpected caller wait status: {status}')
                time.sleep(.001)
            else:
                raise TimeoutError('caller SIGSTOP was not observed within 0.5 s')
            time.sleep(milliseconds / 1000)
            receipt['hold_completed'] = True
        finally:
            # No filesystem writes while the child is stopped. All diagnostic
            # I/O is performed by the controller after this continuation.
            if process.poll() is None:
                receipt.update(continue_requested_wall_ns=time.time_ns(),
                               continue_requested_monotonic_ns=time.monotonic_ns())
                process.send_signal(signal.SIGCONT)
                receipt['continue_sent'] = True
            if receipt.get('stop_observed') and receipt.get('continue_sent'):
                receipt['observed_hold_ns'] = (receipt['continue_requested_monotonic_ns']
                                               - receipt['stop_observed_monotonic_ns'])
    finally:
        signal.signal(signal.SIGTERM, previous)


def pause_on_progress(process, receiver, receipt, path):
    """One-shot controller poll hook; no output I/O during suspension."""
    if receipt['attempted'] or receiver.verified_bytes < receipt['trigger_after_bytes']:
        return
    from v34_payload import write_json
    receipt.update(attempted=True, verified_bytes_at_stop=receiver.verified_bytes)
    write_json(path, receipt)
    if receiver.verified_bytes >= receiver.size:
        raise RuntimeError('caller pause missed the active data window')
    try:
        pause_owned_process(process, receipt['requested_ms'], receipt)
    finally:
        receipt['verified_bytes_at_continue'] = receiver.verified_bytes
        write_json(path, receipt)


def pause_result(receipt, final_verified_bytes):
    """Fault application/recovery evidence, NOT permission to relax timing."""
    receipt['final_verified_upstream_bytes'] = final_verified_bytes
    trigger = receipt.get('verified_bytes_at_stop')
    continued = receipt.get('verified_bytes_at_continue')
    checks = dict(
        triggered_in_data=type(trigger) is int and 0 < trigger < final_verified_bytes,
        stop_observed=receipt.get('stop_observed') is True,
        hold_completed=receipt.get('hold_completed') is True,
        requested_hold_elapsed=receipt.get('observed_hold_ns', -1)
                               >= round(receipt['requested_ms'] * 1_000_000),
        continue_sent=receipt.get('continue_sent') is True,
        verified_progress_after_continue=type(continued) is int and final_verified_bytes > continued,
    )
    receipt.update(checks=checks, passed=all(checks.values()))
    return receipt['passed']
