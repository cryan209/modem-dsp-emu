"""Opt-in qualification fault: main producer/RX dispatch sleeps, wire stays live.

A private inherited pipe arms one pause AFTER real TX publication. No signals,
extra audio, buffer changes, or waits on the wire thread. Disabled by default.
"""
import json
import math
import os
from pathlib import Path
import stat
import threading
import time

from v34_payload import write_json


class ProducerPause:
    def __init__(self, config):
        value = json.loads(Path(config).read_text())
        ms, fd = value['milliseconds'], value['read_fd']
        if type(ms) not in (int, float) or not math.isfinite(ms) or not 1 <= ms <= 1000:
            raise ValueError('producer pause must be finite and 1..1000 ms')
        if type(fd) is not int or fd < 3 or not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise ValueError('producer pause requires an inherited private pipe')
        self.milliseconds = ms
        self.output = Path(value['receipt'])
        self.fd = fd
        os.set_blocking(fd, False)
        self.armed = self.fired = False

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def after_publish(self, call):
        if self.fired:
            return
        if not self.armed:
            try:
                request = os.read(self.fd, 1)
            except BlockingIOError:
                return
            if request != b'1':
                raise RuntimeError('producer pause pipe closed or invalid request')
            self.armed = True
        # Do not manufacture a packet to prove wire activity. Wait for a real
        # queued quantum with a future deadline. The small lead is eligibility,
        # not a changed deadline; independent PCAP evidence must still confirm
        # an actual source send inside the measured pause window.
        if (not call.tx_queue or call.next_send - time.monotonic() < .002
                or call.tx_thread is None or not call.tx_thread.is_alive()):
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('producer pause must run on main producer/RX thread')
        self.fired = True
        result = dict(schema_version=1, kind='producer_rx_dispatch',
                      stage='after_real_tx_publication', requested_ms=self.milliseconds,
                      ssrc=call.ssrc, main_thread_id=threading.get_ident(),
                      wire_thread_id=call.tx_thread.ident,
                      main_thread=True, wire_alive_begin=call.tx_thread.is_alive(),
                      samples_begin=call.samples, rx_samples_begin=len(call.rx),
                      tx_packets_begin=len(call.tx_queue), sends_begin=call.tx_sends,
                      underruns_begin=call.tx_underruns, hold_completed=False)
        if call.media_trace is not None:
            call.media_trace.record('producer_pause_begin', call)
        result.update(begin_wall_ns=time.time_ns(), begin_monotonic_ns=time.monotonic_ns())
        try:
            # sleep releases the GIL. Selector/RX dispatch on this main thread
            # stops; the existing wire-clock thread is neither stopped nor held.
            time.sleep(self.milliseconds / 1000)
            result['hold_completed'] = True
        finally:
            result.update(end_monotonic_ns=time.monotonic_ns(), end_wall_ns=time.time_ns(),
                          wire_alive_end=call.tx_thread.is_alive(),
                          samples_end=call.samples, rx_samples_end=len(call.rx),
                          tx_packets_end=len(call.tx_queue), sends_end=call.tx_sends,
                          underruns_end=call.tx_underruns)
            result['observed_hold_ns'] = result['end_monotonic_ns'] - result['begin_monotonic_ns']
            if call.media_trace is not None:
                call.media_trace.record('producer_pause_end', call)
            try:
                write_json(self.output, result)
            finally:
                self.close()


def request_on_progress(writer, receiver, request, endpoint_path, request_path):
    """Controller poll hook; one private-pipe command, then observe resumption."""
    if request['attempted']:
        if 'verified_bytes_at_resume_observed' not in request and endpoint_path.exists():
            # Endpoint writes its atomic receipt AFTER the sleep has ended.
            request['verified_bytes_at_resume_observed'] = receiver.verified_bytes
            write_json(request_path, request)
        return
    if receiver.verified_bytes < request['trigger_after_bytes']:
        return
    request.update(attempted=True, verified_bytes_at_request=receiver.verified_bytes,
                   request_wall_ns=time.time_ns(), request_monotonic_ns=time.monotonic_ns())
    if receiver.verified_bytes >= receiver.size:
        raise RuntimeError('producer pause missed the active data window')
    if writer.write(b'1') != 1:
        raise RuntimeError('producer pause pipe command was not written')
    write_json(request_path, request)


def application_result(request, endpoint, final_verified_bytes):
    """Application evidence only; source-PCAP wire proof remains mandatory."""
    checks = dict(
        requested=request.get('attempted') is True,
        correct_kind=endpoint.get('kind') == 'producer_rx_dispatch',
        correct_schema=endpoint.get('schema_version') == 1,
        after_real_publication=endpoint.get('stage') == 'after_real_tx_publication',
        queued_real_output=endpoint.get('tx_packets_begin', 0) > 0,
        correct_duration=endpoint.get('requested_ms') == request['requested_ms'],
        main_thread=endpoint.get('main_thread') is True,
        different_wire_thread=(type(endpoint.get('main_thread_id')) is int
                               and type(endpoint.get('wire_thread_id')) is int
                               and endpoint['main_thread_id'] != endpoint['wire_thread_id']),
        wire_live=endpoint.get('wire_alive_begin') is True and endpoint.get('wire_alive_end') is True,
        hold_completed=endpoint.get('hold_completed') is True,
        requested_hold_elapsed=endpoint.get('observed_hold_ns', -1) >= round(request['requested_ms'] * 1e6),
        producer_frozen=(type(endpoint.get('samples_begin')) is int
                         and endpoint['samples_begin'] == endpoint.get('samples_end')),
        rx_queue_frozen=(type(endpoint.get('rx_samples_begin')) is int
                         and endpoint['rx_samples_begin'] == endpoint.get('rx_samples_end')),
        wire_send_progress=endpoint.get('sends_end', 0) > endpoint.get('sends_begin', 0),
        triggered_in_data=0 < request.get('verified_bytes_at_request', 0) < final_verified_bytes,
        progress_after_resume=(type(request.get('verified_bytes_at_resume_observed')) is int
                               and final_verified_bytes > request['verified_bytes_at_resume_observed']),
    )
    request.update(endpoint=endpoint, checks=checks, application_passed=all(checks.values()),
                   final_verified_upstream_bytes=final_verified_bytes)
    return request['application_passed']
