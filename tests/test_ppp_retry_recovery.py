"""Retry recovery for the delayed LCP/PAP exchanges seen on hardware 7930."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from ppp import (PppPeer, PppConfig, AuthClient, PppError, CONF_REQ, CONF_ACK,
                 HdlcFramer, PROTO_LCP, parse_packet)
import pytest


def client():
    return PppPeer(PppConfig(role='client', username='test', password='',
                             auth=None, trace=False), log=lambda _: None)


@pytest.mark.parametrize('code', range(1, 8))
def test_lcp_negotiation_survives_peer_reset_to_default_accm(code):
    tx = HdlcFramer(); tx.tx_accm = 0; tx.tx_acfc = True; tx.tx_pfc = True
    packet = struct.pack('>BBH', code, 3, 8) + b'\x01\x02\x03\x04'
    wire = tx.encode(PROTO_LCP, packet)
    # The other end may have restarted pppd and restored its default ACCM.
    rx = HdlcFramer()
    frames = rx.feed(wire)
    assert len(frames) == 1
    assert parse_packet(frames[0]) == (PROTO_LCP, packet)
    assert rx.fcs_errors == 0


def test_ack_received_keeps_timer_and_recovers_missing_peer_request():
    p = client()
    p.start(0)
    p.take()
    old_id = p.lcp.last_request_id
    p.lcp._recv_2(old_id, p.lcp.last_request, 1)
    assert p.lcp.state == 'ack-rcvd'
    assert p.lcp.deadline == 4
    p.tick(4)
    assert p.lcp.state == 'req-sent'
    assert p.lcp.last_request_id != old_id
    assert p.take()
    p.lcp._recv_1(7, b'', 5)
    p.lcp._recv_2(p.lcp.last_request_id, p.lcp.last_request, 6)
    assert p.lcp.state == 'opened'


def test_pure_timeout_preserves_request_id_and_options():
    p = client(); p.start(0); p.take()
    original = (p.lcp.last_request_id, p.lcp.last_request)
    p.tick(3)
    assert (p.lcp.last_request_id, p.lcp.last_request) == original
    assert p.take()


def pap_client():
    p = client()
    p.lcp.state = 'opened'; p.lcp.peer_auth = 'pap'
    p.on_lcp_up(); p.take()
    return p


def test_pap_retries_and_stops_after_valid_ack():
    p = pap_client(); a = p.auth
    assert isinstance(a, AuthClient)
    request = a.request
    a.tick(3)
    assert a.request == request
    assert p.take()
    a.feed(struct.pack('>BBH', 2, 1, 5) + b'\x00', 4)
    assert p.authenticated
    p.take(); a.tick(100)
    assert not p.take()
    assert a.deadline is None


def test_pap_stale_ack_does_not_authenticate():
    p = pap_client()
    p.auth.feed(struct.pack('>BBH', 2, 9, 5) + b'\x00', 1)
    assert not p.authenticated
    assert p.auth.deadline is not None


def test_pap_retries_are_bounded():
    p = pap_client(); a = p.auth
    for n in range(1, 11):
        a.tick(n * 3)
    assert a.succeeded is False
    assert a.deadline is None
    assert not p.authenticated


def test_pap_bad_length_is_rejected():
    p = pap_client()
    with pytest.raises(PppError, match='length'):
        p.auth.feed(struct.pack('>BBH', 2, 1, 100), 1)


def test_pap_retry_stops_when_lcp_goes_down():
    p = pap_client(); a = p.auth
    p.lcp.state = 'req-sent'
    a.tick(3)
    assert not p.take()
    assert a.deadline is None
