"""Portable tests for qualification integrity, verdicts and process cleanup."""
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qualify_v34 import endpoint_result, parser, payload_result, stop_process
from usernet import build_ipv4, build_udp, parse_ipv4
from v34_payload import BLOCK, HEADER, MAGIC, Client, Receiver, Server, expected_hash, payload

TOKEN = bytes(range(16))
HOST = socket.inet_aton('127.0.0.1')
LOCAL = socket.inet_aton('100.64.0.2')


def setup_probe(tmp_path, size=137):
    config = tmp_path / 'probe.json'
    config.write_text(json.dumps(dict(token=TOKEN.hex(), bytes=size, port=47000,
                                     report=str(tmp_path / 'client.json'))))
    client = Client(config)
    peer = SimpleNamespace(up=True, ipcp=SimpleNamespace(local_address='100.64.0.2'),
                           rx_ip=[], sent=[])
    peer.send_ip = peer.sent.append
    return client, Receiver(TOKEN, size), peer


def response(client, body):
    return build_ipv4(HOST, LOCAL, 17, build_udp(HOST, LOCAL, client.port,
                                               client.source_port, body))


def deliver(client, receiver, peer):
    for request in peer.sent:
        udp = parse_ipv4(request)[3]
        reply = receiver.accept(udp[8:])
        if reply is not None:
            peer.rx_ip.append(response(client, reply))
    peer.sent.clear()


def finish(client, receiver, peer):
    for tick in range(client.blocks + 2):
        client.pump(peer, now=100 + tick / 10)
        deliver(client, receiver, peer)
        if client.finished:
            return
    pytest.fail('probe did not finish')


@pytest.mark.parametrize('size', [1, BLOCK, BLOCK + 1, 4096])
def test_independent_exact_payloads_in_both_directions(tmp_path, size):
    client, receiver, peer = setup_probe(tmp_path, size)
    finish(client, receiver, peer)
    assert payload(TOKEN, b'up', 0, size) != payload(TOKEN, b'down', 0, size)
    verdict = payload_result(TOKEN, size, client.result(), receiver.result())
    assert verdict['passed'], verdict
    assert client.result()['retries'] == 0
    assert json.loads(client.report.read_text()) == client.result()
    assert client.result()['transfer_seconds'] > 0
    # Fits N401=128 with the PPP protocol field, without IP fragmentation.
    assert 20 + 8 + HEADER.size + BLOCK + 2 <= 128


def test_lost_response_retry_and_duplicate_do_not_double_count(tmp_path):
    client, receiver, peer = setup_probe(tmp_path, 64)
    client.pump(peer, now=100)
    deliver(client, receiver, peer)
    original = peer.rx_ip.pop()
    client.pump(peer, now=101)
    assert not peer.sent
    client.pump(peer, now=102)
    deliver(client, receiver, peer)
    peer.rx_ip.append(original)
    client.pump(peer, now=102.1)
    assert payload_result(TOKEN, 64, client.result(), receiver.result())['passed']
    assert client.result()['bytes'] == 64
    assert client.result()['retries'] == 1
    assert receiver.result()['duplicates'] == 1


@pytest.mark.parametrize('kind', ['corruption', 'truncated', 'oversized', 'nonce', 'sequence'])
def test_receiver_rejects_invalid_upstream_even_if_later_retried(kind):
    receiver = Receiver(TOKEN, 64)
    valid = HEADER.pack(MAGIC, TOKEN, 0) + payload(TOKEN, b'up', 0, 64)
    bad = {
        'corruption': valid[:-1] + bytes([valid[-1] ^ 1]),
        'truncated': valid[:3],
        'oversized': valid + b'x',
        'nonce': HEADER.pack(MAGIC, b'x' * 16, 0) + valid[HEADER.size:],
        'sequence': HEADER.pack(MAGIC, TOKEN, 1) + valid[HEADER.size:],
    }[kind]
    assert receiver.accept(bad) is None
    assert receiver.accept(valid) is not None
    assert receiver.result()['bytes'] == 64
    assert not receiver.result()['complete']
    assert receiver.result()['errors']


@pytest.mark.parametrize('kind', ['corruption', 'echo', 'nonce', 'sequence', 'length', 'source'])
def test_client_rejects_invalid_downstream(tmp_path, kind):
    client, receiver, peer = setup_probe(tmp_path, 64)
    client.pump(peer, now=100)
    valid = HEADER.pack(MAGIC, TOKEN, 0) + payload(TOKEN, b'down', 0, 64)
    bad = {
        'corruption': valid[:-1] + bytes([valid[-1] ^ 1]),
        'echo': HEADER.pack(MAGIC, TOKEN, 0) + payload(TOKEN, b'up', 0, 64),
        'nonce': HEADER.pack(MAGIC, b'x' * 16, 0) + valid[HEADER.size:],
        'sequence': HEADER.pack(MAGIC, TOKEN, 1) + valid[HEADER.size:],
        'length': valid + b'x',
        'source': valid,
    }[kind]
    packet = response(client, bad)
    if kind == 'source':
        wrong = socket.inet_aton('127.0.0.2')
        packet = build_ipv4(wrong, LOCAL, 17, build_udp(wrong, LOCAL, client.port,
                                                       client.source_port, bad))
    peer.rx_ip.extend([packet, response(client, valid)])
    client.pump(peer, now=100.1)
    assert client.finished  # A later correct copy must not erase the failure.
    assert not client.result()['complete']
    assert client.result()['errors']
    assert not payload_result(TOKEN, 64, client.result(), receiver.result())['passed']


def test_incomplete_report_cannot_pass(tmp_path):
    client, receiver, peer = setup_probe(tmp_path)
    client.pump(peer, now=100)
    client.close()
    assert not json.loads(client.report.read_text())['complete']
    assert not payload_result(TOKEN, 137, client.result(), receiver.result())['passed']
    assert not payload_result(TOKEN, 137, {}, {})['passed']


def test_probe_waits_for_ipcp_and_preserves_other_traffic(tmp_path):
    client, receiver, peer = setup_probe(tmp_path)
    peer.up = False
    client.pump(peer, now=100)
    assert not peer.sent
    assert client.started_at is None
    peer.up = True
    unrelated = build_ipv4(LOCAL, HOST, 1, b'icmp')
    peer.rx_ip.append(unrelated)
    client.pump(peer, now=101)
    assert peer.rx_ip == [unrelated]


def test_real_localhost_server_verifies_and_closes():
    receiver = Receiver(TOKEN, 64)
    with Server(receiver) as server, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        request = HEADER.pack(MAGIC, TOKEN, 0) + payload(TOKEN, b'up', 0, 64)
        sock.sendto(request, ('127.0.0.1', server.port))
        data, _ = sock.recvfrom(1024)
        assert data == HEADER.pack(MAGIC, TOKEN, 0) + payload(TOKEN, b'down', 0, 64)
    assert not server.thread.is_alive()
    assert server.socket.fileno() == -1
    assert receiver.result()['complete']


CLEAN_LOG = '''[adsp] TrnProgress 0x00cc -> 0x00d0
[v42] LAPM connected (UA(F) received), link 1
[ppp] authenticated 'ppp'
[ppp] IPCP up local=100.64.0.2 peer=100.64.0.1
[media] call totals: substituted 0 RX samples, dropped 0, tx buffer 1/1 (low 1, 4 underruns, wire up)
[ppp] IPCP down
[ppp] LCP down
[ppp] lcp=closing ipcp=closed auth=ppp tx=10 rx=10 fcs-errors=0; 1024 bytes to the link (0 data-link resets)
'''


def test_endpoint_verdict_exposes_underruns_and_optional_strict_budget():
    result = endpoint_result(CLEAN_LOG)
    assert result['passed']
    assert result['media']['tx_underruns'] == 4
    assert endpoint_result(CLEAN_LOG, 4)['passed']
    assert not endpoint_result(CLEAN_LOG, 0)['passed']
    assert not endpoint_result(CLEAN_LOG, require_nat_cleanup=True)['passed']
    closed = CLEAN_LOG + '[ppp] closed 1 flow(s) for 100.64.0.2\n'
    assert endpoint_result(closed, require_nat_cleanup=True)['passed']


@pytest.mark.parametrize('text', [
    '', CLEAN_LOG.replace('[ppp] LCP down', ''),
    CLEAN_LOG.replace('fcs-errors=0', 'fcs-errors=1'),
    CLEAN_LOG.replace('0 data-link resets', '1 data-link resets'),
    CLEAN_LOG.replace('substituted 0', 'substituted 1'),
    CLEAN_LOG.replace('dropped 0', 'dropped 1'),
    CLEAN_LOG + '[v42] LAPM connected again\n',
    CLEAN_LOG + '[ppp] IPCP up again\n',
    CLEAN_LOG.replace('[media] call totals:', '[media] periodic:'),
    CLEAN_LOG + 'Traceback (most recent call last):\n',
])
def test_endpoint_verdict_fails_closed(text):
    assert not endpoint_result(text)['passed']


def test_result_checks_hashes_counts_and_nonce_independently(tmp_path):
    client, receiver, peer = setup_probe(tmp_path, 64)
    finish(client, receiver, peer)
    good = client.result()
    for key, value in [('sha256', '0' * 64), ('bytes', 63), ('token', '00' * 16),
                       ('complete', False), ('errors', ['corrupt'])]:
        assert not payload_result(TOKEN, 64, dict(good, **{key: value}), receiver.result())['passed']
    assert receiver.result()['sha256'] == expected_hash(TOKEN, b'up', 64)


def test_outgoing_shutdown_sends_bye_before_local_teardown():
    from eicon_adsp_sip import EiconSipEndpoint
    endpoint = object.__new__(EiconSipEndpoint)
    endpoint.call = SimpleNamespace()
    endpoint.outgoing = {'dialog': 'exists'}
    events = []
    endpoint.send_bye = lambda: events.append('bye')
    endpoint.end_call = lambda reason: events.append(reason)
    endpoint.hangup_call('shutdown')
    assert events == ['bye', 'shutdown']


def test_failed_outgoing_bye_still_tears_down():
    from eicon_adsp_sip import EiconSipEndpoint
    endpoint = object.__new__(EiconSipEndpoint)
    endpoint.call = SimpleNamespace()
    endpoint.outgoing = {'dialog': 'exists'}
    endpoint.send_bye = Mock(side_effect=OSError('closed'))
    endpoint.end_call = Mock()
    endpoint.hangup_call('shutdown')
    endpoint.end_call.assert_called_once_with('shutdown')


@pytest.mark.parametrize('arguments', [
    ['--calls', '0'], ['--bytes', '0'], ['--call-timeout', 'nan'],
    ['--call-timeout', 'inf'], ['--max-tx-underruns', '-1'],
])
def test_invalid_arguments_fail_before_starting_calls(monkeypatch, arguments):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', *arguments])
    with pytest.raises(SystemExit) as exc:
        parser()
    assert exc.value.code == 2


def test_general_loopback_validation_retains_argument_parser(monkeypatch):
    import eicon_loopback
    monkeypatch.setattr(sys, 'argv', ['eicon_loopback.py', '--ppp-ping', 'peer'])
    with pytest.raises(SystemExit) as exc:
        eicon_loopback.main()
    assert exc.value.code == 2


def test_existing_output_is_rejected_without_overwriting(tmp_path, monkeypatch):
    receipt = tmp_path / 'result.json'
    receipt.write_text('old evidence')
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--output', str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        parser()
    assert exc.value.code == 2
    assert receipt.read_text() == 'old evidence'


def test_interpreter_symlink_keeps_virtualenv_path(tmp_path, monkeypatch):
    interpreter = tmp_path / 'venv-python'
    interpreter.symlink_to(sys.executable)
    output = tmp_path / 'new-evidence'
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--python', str(interpreter),
                                      '--output', str(output)])
    assert parser().python == interpreter
    assert not output.exists()


def test_forced_child_cleanup_is_reaped_and_not_a_pass():
    code = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"
    process = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        result = stop_process(process, timeout=0.05)
        assert result['forced_kill'] and not result['passed']
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
