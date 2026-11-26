"""Portable source-clock qualification tests, independent of live firmware."""
import json
from pathlib import Path
import socket
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import qualify_v34
from rtp_qualification import evaluate_pair, read_capture, source_window

CALLER, ANSWERER = 24012, 24010
BASE = 1_700_000_000_000_000_000


def frames(port, ssrc, count=101, period=20_000_000, offset=0):
    peer = ANSWERER if port == CALLER else CALLER
    return [dict(wall=BASE + offset + i * period, sport=port, dport=peer,
                 ssrc=ssrc, seq=(65530 + i) & 65535,
                 timestamp=(0xffffff00 + i * 160) & 0xffffffff,
                 size=160, pt=0) for i in range(count)]


def encode(records, endian='<', nanos=False):
    magic = 0xa1b23c4d if nanos else 0xa1b2c3d4
    blob = bytearray(struct.pack(endian + 'IHHIIII', magic, 2, 4, 0, 0, 65535, 101))
    for row in records:
        rtp = struct.pack('!BBHII', 0x80, row['pt'], row['seq'], row['timestamp'], row['ssrc']) + bytes(row['size'])
        udp = struct.pack('!HHHH', row['sport'], row['dport'], 8 + len(rtp), 0) + rtp
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                         socket.inet_aton('127.0.0.1'), socket.inet_aton('127.0.0.1')) + udp
        seconds, ns = divmod(row['wall'], 1_000_000_000)
        blob.extend(struct.pack(endian + 'IIII', seconds, ns if nanos else ns // 1000, len(ip), len(ip)))
        blob.extend(ip)
    return bytes(blob)


def capture(tmp_path, records, name='capture.pcap', **kwargs):
    path = tmp_path / name
    path.write_bytes(encode(records, **kwargs))
    return read_capture(path)


def pair(tmp_path, *, caller_rows=None, answer_rows=None, receive_rows=None, **budgets):
    caller_rows = frames(CALLER, 10) if caller_rows is None else caller_rows
    answer_rows = frames(ANSWERER, 20) if answer_rows is None else answer_rows
    receive_rows = frames(ANSWERER, 20, offset=2_000_000) if receive_rows is None else receive_rows
    caller = capture(tmp_path, caller_rows + receive_rows, 'caller.pcap')
    answerer = capture(tmp_path, answer_rows, 'answerer.pcap')
    return evaluate_pair(caller, answerer, CALLER, ANSWERER, 101, 101, **budgets)


@pytest.mark.parametrize('endian,nanos', [('<', False), ('>', False), ('<', True), ('>', True)])
def test_capture_formats_and_sequence_timestamp_wraps(tmp_path, endian, nanos):
    stream, = capture(tmp_path, frames(CALLER, 10), endian=endian, nanos=nanos)
    result = stream.verdict(101, 1000, None)
    assert result['passed']
    assert result['timestamp_hz'] == 8000
    assert result['min_gap_ms'] == result['max_gap_ms'] == 20
    assert result['sequence_gaps'] == result['timestamp_jumps'] == 0


def test_timing_comes_from_source_not_receiver_arrival_clock(tmp_path):
    bad_source = frames(ANSWERER, 20, period=21_000_000)
    result = pair(tmp_path, answer_rows=bad_source)
    assert not result['passed']
    assert result['caller']['passed']
    assert not result['answerer']['checks']['clock_budget']
    # Conversely, receiver batching must not condemn a correct emitter.
    bad_arrivals = frames(ANSWERER, 20, period=21_000_000)
    assert pair(tmp_path, receive_rows=bad_arrivals)['passed']


@pytest.mark.parametrize('mutation,check', [
    ('drop', 'sequence_continuity'), ('duplicate', 'sequence_continuity'),
    ('reorder', 'sequence_continuity'), ('timestamp', 'timestamp_continuity'),
    ('wall_backwards', 'increasing_capture_clock'), ('wall_equal', 'increasing_capture_clock'),
    ('payload', 'payload_size'), ('clean_tail_loss', 'complete_capture'),
])
def test_capture_failures_cannot_be_sorted_or_counted_away(tmp_path, mutation, check):
    rows = frames(CALLER, 10)
    if mutation == 'drop':
        del rows[50]
    elif mutation == 'duplicate':
        rows.insert(50, rows[50].copy())
    elif mutation == 'reorder':
        rows[50], rows[51] = rows[51], rows[50]
    elif mutation == 'timestamp':
        rows[50]['timestamp'] += 1
    elif mutation == 'wall_backwards':
        rows[50]['wall'] = rows[49]['wall'] - 1000
    elif mutation == 'wall_equal':
        rows[50]['wall'] = rows[49]['wall']
    elif mutation == 'payload':
        rows[50]['size'] = 80
    elif mutation == 'clean_tail_loss':
        rows.pop()
    result = pair(tmp_path, caller_rows=rows)
    assert not result['passed']
    assert not result['caller']['checks'][check]


def test_single_gap_and_burst_can_have_correct_average_clock(tmp_path):
    rows = frames(CALLER, 10)
    rows[50]['wall'] += 12_000_000
    result = pair(tmp_path, caller_rows=rows, max_gap_ms=30)
    assert not result['passed']
    assert result['caller']['checks']['clock_budget']
    assert not result['caller']['checks']['gap_budget']
    assert result['caller']['min_gap_ms'] == 8
    assert result['caller']['max_gap_ms'] == 32
    assert pair(tmp_path, caller_rows=rows, max_gap_ms=32)['passed']


@pytest.mark.parametrize('data', [b'', b'bad header', encode([])[:-1],
                                  encode(frames(CALLER, 10))[:-1],
                                  encode(frames(CALLER, 10)) + b'x'])
def test_incomplete_capture_is_rejected(tmp_path, data):
    path = tmp_path / 'bad.pcap'
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read_capture(path)


@pytest.mark.parametrize('offset,value', [(20, 1), (36, 1), (42, 0x65), (46, 1), (68, 0x81)])
def test_malformed_container_or_packet_is_rejected(tmp_path, offset, value):
    # link type, original length, IP total length, IP fragmentation, RTP header
    data = bytearray(encode(frames(CALLER, 10)))
    data[offset] = value
    path = tmp_path / 'bad.pcap'
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read_capture(path)


def test_missing_ambiguous_or_wrong_identity_cannot_borrow_another_stream(tmp_path):
    caller = capture(tmp_path, frames(CALLER, 10) + frames(ANSWERER, 20), 'caller.pcap')
    wrong = capture(tmp_path, frames(ANSWERER, 30), 'wrong.pcap')
    assert not evaluate_pair(caller, wrong, CALLER, ANSWERER, 101, 101)['passed']
    ambiguous = capture(tmp_path, frames(CALLER, 10) + frames(CALLER, 11) + frames(ANSWERER, 20))
    assert not evaluate_pair(ambiguous, wrong, CALLER, ANSWERER, 101, 101)['passed']
    assert not evaluate_pair([], [], CALLER, ANSWERER, 0, 0)['passed']
    assert not pair(tmp_path, receive_rows=[])['passed']
    rows = frames(CALLER, 10)
    for row in rows:
        row['pt'] = 8
    assert not pair(tmp_path, caller_rows=rows)['passed']


def test_source_window_uses_exact_sender_and_preserves_window_boundaries(tmp_path):
    path = tmp_path / 'window.pcap'
    path.write_bytes(encode(frames(CALLER, 10) + frames(ANSWERER, 20)))
    source = next(s for s in read_capture(path) if s.sport == CALLER).verdict(101, 1000, None)
    result = source_window(path, source, BASE + 20_000_000, BASE + 40_000_000)
    assert result['passed'] and result['packets'] == 2
    assert result['first_wall_ns'] == BASE + 20_000_000
    assert result['last_wall_ns'] == BASE + 40_000_000
    assert not source_window(path, source, BASE + 1, BASE + 10_000_000)['passed']
    assert not source_window(path, dict(source, ssrc=99), BASE, BASE + 1_000_000_000)['passed']
    assert not source_window(path, source, None, BASE)['passed']
    assert not source_window(path, source, BASE, BASE)['passed']
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):
        source_window(path, source, BASE, BASE + 100_000_000)


def test_one_packet_or_missing_final_send_count_cannot_qualify_clock(tmp_path):
    stream, = capture(tmp_path, frames(CALLER, 10, count=1))
    assert not stream.verdict(1, 1000, None)['passed']
    stream, = capture(tmp_path, frames(CALLER, 10))
    assert not stream.verdict(None, 1000, None)['passed']


@pytest.mark.parametrize('options', [
    ['--max-tx-clock-ppm', 'nan'], ['--max-tx-clock-ppm', 'inf'],
    ['--max-tx-clock-ppm', '-1'], ['--max-tx-clock-ppm', '1000000'],
    ['--max-tx-gap-ms', 'nan'], ['--max-tx-gap-ms', 'inf'],
    ['--max-tx-gap-ms', '0'], ['--max-tx-gap-ms', '-1'],
])
def test_invalid_budgets_fail_before_any_calls(monkeypatch, options):
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', *options])
    with pytest.raises(SystemExit) as exc:
        qualify_v34.parser()
    assert exc.value.code == 2


@pytest.mark.parametrize('failure', [None, 'clock', 'missing_capture', 'extra_stream', 'missing_send_count', 'no_wire_during_pause'])
def test_top_level_verdict_includes_capture_timing_even_when_protocol_passed(tmp_path, monkeypatch, failure):
    output = tmp_path / 'new-run'
    monkeypatch.setattr(sys, 'argv', ['qualify_v34.py', '--calls', '1', '--output', str(output)])

    def fake_run(args, report):
        directory = args.output / 'call-001'
        directory.mkdir()
        if failure != 'missing_capture':
            (directory / 'caller.rtp.pcap').write_bytes(encode(frames(CALLER, 10) + frames(ANSWERER, 20)))
        answer = frames(ANSWERER, 20, period=21_000_000 if failure == 'clock' else 20_000_000)
        if failure == 'extra_stream':
            answer += frames(ANSWERER, 99)
        (args.output / 'answerer.rtp.pcap').write_bytes(encode(answer))
        report['ports'] = dict(caller_rtp=CALLER, answerer_rtp=ANSWERER)
        report['answerer_process'] = dict(passed=True)
        report['calls'].append(dict(number=1, protocol_passed=True, passed=False,
                                    caller=dict(media=dict(tx_sends=None if failure == 'missing_send_count' else 101)),
                                    answerer=dict(media=dict(tx_sends=101))))
        if failure == 'no_wire_during_pause':
            report['calls'][0]['producer_pause'] = dict(endpoint=dict(
                ssrc=10, begin_wall_ns=BASE + 3_000_000_000, end_wall_ns=BASE + 4_000_000_000))

    monkeypatch.setattr(qualify_v34, 'run', fake_run)
    assert qualify_v34.main() == (1 if failure else 0)
    receipt = json.loads((output / 'result.json').read_text())
    assert receipt['schema_version'] == 2
    assert receipt['passed'] is (not bool(failure))
    assert receipt['calls'][0]['protocol_passed'] is True
    assert receipt['calls'][0]['source_timing_pending'] is False
    saved = json.loads((output / 'call-001/result.json').read_text())
    assert saved['passed'] is (not bool(failure))
