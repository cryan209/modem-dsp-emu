"""Private-pipe producer/RX pause; production wire loop stays independently live."""
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_adsp_sip import Call, EiconSipEndpoint, RtpCapture
from producer_pause import ProducerPause, application_result, request_on_progress
from rtp_qualification import read_capture, source_window
import qualify_v34


@pytest.fixture
def pipe(tmp_path):
    read_fd, write_fd = os.pipe()
    config = tmp_path / 'config.json'
    output = tmp_path / 'endpoint.json'
    config.write_text(json.dumps(dict(read_fd=read_fd, milliseconds=1, receipt=str(output))))
    helper = ProducerPause(config)
    writer = os.fdopen(write_fd, 'wb', buffering=0)
    try:
        yield helper, writer, output
    finally:
        helper.close()
        writer.close()


def fake_call():
    return SimpleNamespace(tx_queue=[b'real output'], next_send=time.monotonic() + 1,
                           tx_thread=SimpleNamespace(ident=threading.get_ident() + 1, is_alive=lambda: True),
                           ssrc=17, samples=80000, rx=[1, 2], tx_sends=10,
                           tx_underruns=0, media_trace=None)


def test_no_request_is_nonblocking_and_never_sleeps(pipe, monkeypatch):
    helper, _, output = pipe
    monkeypatch.setattr(time, 'sleep', lambda _: pytest.fail('unrequested pause'))
    helper.after_publish(fake_call())
    assert not helper.armed and not helper.fired and not output.exists()


def test_armed_request_waits_for_real_queued_future_output_then_fires_once(pipe, monkeypatch):
    helper, writer, output = pipe
    writer.write(b'1')
    call = fake_call()
    packet = call.tx_queue.pop()
    helper.after_publish(call)
    assert helper.armed and not helper.fired
    call.tx_queue.append(packet)
    call.next_send = time.monotonic() - 1
    helper.after_publish(call)
    assert not helper.fired
    call.next_send = time.monotonic() + 1
    sleeps = []
    monkeypatch.setattr(time, 'sleep', lambda seconds: sleeps.append(seconds))
    helper.after_publish(call)
    helper.after_publish(call)
    assert sleeps == [.001] and helper.fired
    assert json.loads(output.read_text())['tx_packets_begin'] == 1
    assert call.tx_queue == [packet]  # fault never adds or consumes audio
    assert helper.fd is None


@pytest.mark.parametrize('message', [b'x', b''])
def test_bad_or_closed_command_pipe_fails_instead_of_claiming_coverage(pipe, message):
    helper, writer, _ = pipe
    if message:
        writer.write(message)
    else:
        writer.close()
    with pytest.raises(RuntimeError, match='pipe closed or invalid'):
        helper.after_publish(fake_call())
    assert not helper.fired


def test_interrupt_receipt_is_written_after_hold_and_pipe_is_closed(pipe, monkeypatch):
    helper, writer, output = pipe
    writer.write(b'1')
    def interrupt(_):
        assert not output.exists()
        raise KeyboardInterrupt
    monkeypatch.setattr(time, 'sleep', interrupt)
    with pytest.raises(KeyboardInterrupt):
        helper.after_publish(fake_call())
    record = json.loads(output.read_text())
    assert not record['hold_completed'] and helper.fd is None


def test_production_wire_loop_sends_real_packet_while_main_producer_is_blocked(pipe, tmp_path, monkeypatch):
    helper, writer, output = pipe
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(5)
    ep = object.__new__(EiconSipEndpoint)
    ep.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ep.rtp.bind(('127.0.0.1', 0))
    ep.bind = ep.advertised = '127.0.0.1'
    ep.rtp_port = ep.rtp.getsockname()[1]
    ep.payload_type = 0
    ep.rtp_packet_seconds = .020
    ep.tx_target_quanta = 1
    ep.repeat_tx_underrun = False
    ep.capture = RtpCapture(tmp_path / 'source', 'pcmu')
    call = Call(sip_peer=('127.0.0.1', 1), rtp_peer=receiver.getsockname(),
                call_id='synthetic', local_tag='test', card=None, samples=80000)
    original = bytes(range(160))
    call.tx_queue.append(original)
    call.next_send = time.monotonic() + .050
    sent = threading.Event()
    real_send = ep.transmit_one
    def send(c):
        real_send(c)  # production packet header, UDP send and source capture
        sent.set()
    ep.transmit_one = send
    ep.call = call
    ep.start_transmit_clock(call)
    writer.write(b'1')
    def blocked_main(_seconds):
        assert threading.current_thread() is threading.main_thread()
        assert not output.exists()
        assert sent.wait(5)  # coarse liveness bound, not a scheduling benchmark
    monkeypatch.setattr(time, 'sleep', blocked_main)
    try:
        helper.after_publish(call)
        assert helper.fired and call.tx_sends == 1 and not call.tx_queue
        packet, _ = receiver.recvfrom(65535)
        assert packet[12:] == original
    finally:
        ep.stop_transmit_clock(call)
        ep.capture.close()
        ep.rtp.close()
        receiver.close()
    record = json.loads(output.read_text())
    request = dict(attempted=True, requested_ms=1, verified_bytes_at_request=1024,
                   verified_bytes_at_resume_observed=1024)
    assert application_result(request, record, 4096)
    assert record['samples_begin'] == record['samples_end'] == 80000
    assert record['sends_end'] - record['sends_begin'] == 1
    stream, = read_capture(tmp_path / 'source.rtp.pcap')
    window = source_window(tmp_path / 'source.rtp.pcap', stream.verdict(1, 1000, None),
                           record['begin_wall_ns'], record['end_wall_ns'])
    assert window['passed'] and window['packets'] == 1


@pytest.mark.parametrize('milliseconds', [0, -1, 1001, float('nan'), float('inf'), True])
def test_bad_config_duration_does_not_claim_pipe_ownership(pipe, tmp_path, milliseconds):
    helper, _, _ = pipe
    config = tmp_path / 'bad-config.json'
    config.write_text(json.dumps(dict(read_fd=helper.fd, milliseconds=milliseconds, receipt='unused')))
    with pytest.raises(ValueError, match='finite and 1..1000'):
        ProducerPause(config)
    os.fstat(helper.fd)  # failed construction did not close somebody else's FD


def test_regular_file_cannot_be_used_as_control_pipe(tmp_path):
    with (tmp_path / 'regular').open('wb') as regular:
        config = tmp_path / 'bad-config.json'
        config.write_text(json.dumps(dict(read_fd=regular.fileno(), milliseconds=100, receipt='unused')))
        with pytest.raises(ValueError, match='private pipe'):
            ProducerPause(config)


def test_wire_worker_cannot_accidentally_execute_the_pause(pipe):
    helper, writer, _ = pipe
    writer.write(b'1')
    failures = []
    def worker():
        try:
            helper.after_publish(fake_call())
        except RuntimeError as exc:
            failures.append(str(exc))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and not helper.fired
    assert failures == ['producer pause must run on main producer/RX thread']


def test_request_hook_sends_once_and_observes_post_resume_progress(tmp_path):
    writer = io.BytesIO()
    receiver = SimpleNamespace(verified_bytes=960, size=4096)
    request = dict(attempted=False, requested_ms=100, trigger_after_bytes=1024)
    endpoint = tmp_path / 'endpoint.json'
    path = tmp_path / 'request.json'
    request_on_progress(writer, receiver, request, endpoint, path)
    assert not writer.getvalue() and not path.exists()
    receiver.verified_bytes = 1024
    request_on_progress(writer, receiver, request, endpoint, path)
    assert writer.getvalue() == b'1'
    endpoint.write_text('{}')
    request_on_progress(writer, receiver, request, endpoint, path)
    receiver.verified_bytes = 2048
    request_on_progress(writer, receiver, request, endpoint, path)
    assert writer.getvalue() == b'1' and request['verified_bytes_at_resume_observed'] == 1024


@pytest.mark.parametrize('field,value', [
    ('hold_completed', False), ('wire_alive_end', False), ('samples_end', 80160),
    ('rx_samples_end', 321), ('sends_end', 10), ('observed_hold_ns', 99_999_999),
    ('main_thread', False), ('wire_thread_id', 1),
])
def test_missing_isolation_evidence_cannot_pass_application(field, value):
    record = dict(schema_version=1, kind='producer_rx_dispatch', stage='after_real_tx_publication',
                  tx_packets_begin=1, requested_ms=100, main_thread=True,
                  main_thread_id=1, wire_thread_id=2, wire_alive_begin=True,
                  wire_alive_end=True, hold_completed=True, observed_hold_ns=100_000_000,
                  samples_begin=80000, samples_end=80000, rx_samples_begin=320,
                  rx_samples_end=320, sends_begin=10, sends_end=11)
    record[field] = value
    request = dict(requested_ms=100, attempted=True, verified_bytes_at_request=1024,
                   verified_bytes_at_resume_observed=1024)
    assert not application_result(request, record, 4096)


@pytest.mark.parametrize('options', [
    ['--producer-pause-ms', 'nan'], ['--producer-pause-ms', 'inf'],
    ['--producer-pause-ms', '-1'], ['--producer-pause-ms', '.5'],
    ['--producer-pause-ms', '1001'],
    ['--producer-pause-ms', '100', '--caller-pause-ms', '100'],
    ['--producer-pause-ms', '100', '--pause-after-bytes', '65'],
])
def test_invalid_producer_fault_options_fail_before_launch(monkeypatch, options):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', *options])
    with pytest.raises(SystemExit) as exc:
        qualify_v34.parser()
    assert exc.value.code == 2
