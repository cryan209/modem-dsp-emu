import argparse
import select
import socket
import struct
import unittest

from tools import diva_sip_gateway as g


def indication(command, sub, body, number=7):
    return g.capi_message(1, command, sub, number, body)


def args(**overrides):
    values = dict(username='2910', password='secret', controller=3, cip=4, isdn_number='',
                  default_extension='', bind='127.0.0.1', rtp_first=41000, rtp_last=41198,
                  expires=300, max_calls=2, bit_reverse=True)
    values.update(overrides)
    return argparse.Namespace(**values)


class Harness:
    """A gateway between a fake CAPI device and a fake Asterisk."""

    def __init__(self, **overrides):
        self.card, near = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        near.setblocking(False)
        self.near = near
        self.asterisk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.asterisk.bind(('127.0.0.1', 0))
        self.asterisk.settimeout(2)
        sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sip.bind(('127.0.0.1', 0))
        sip.setblocking(False)
        self.gw = g.Gateway(args(**overrides), g.Capi(fd=near.fileno()), sip,
                            registrar=self.asterisk.getsockname(), advertise='127.0.0.1')
        self.gw_sip = sip.getsockname()

    def close(self):
        for call in self.gw.calls.values():
            call.rtp.close()
        for sock in (self.card, self.near, self.asterisk, self.gw.sip):
            sock.close()

    def capi_out(self):
        return g.CapiMessage.parse(self.card.recv(4096))

    def capi_in(self, raw):
        self.card.send(raw)
        self.gw.on_capi()

    def sip_in(self, text):
        self.asterisk.sendto(text.encode() if isinstance(text, str) else text, self.gw_sip)
        select.select([self.gw.sip], [], [], 2)
        self.gw.on_sip()

    def sip_out(self):
        return g.SipMessage.parse(self.asterisk.recv(65535))


def sdp(port):
    return g.build_sdp('127.0.0.1', port, 1)


def invite(rtp_port, uri='sip:2910@127.0.0.1'):
    body = sdp(rtp_port)
    return (f'INVITE {uri} SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKabc\r\n'
            'From: "Six" <sip:6001@127.0.0.1>;tag=from1\r\nTo: <sip:2910@127.0.0.1>\r\n'
            'Call-ID: call-1\r\nCSeq: 10 INVITE\r\nContact: <sip:asterisk@127.0.0.1:5060>\r\n'
            f'Content-Type: application/sdp\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body


class CapiEncodingTest(unittest.TestCase):
    def test_connect_req_layout(self):
        body = g.connect_req(3, 4, '7950', '6001')
        controller, cip = struct.unpack_from('<IH', body)
        self.assertEqual((controller, cip), (3, 4))
        r = g.Reader(body, 6)
        self.assertEqual(r.struct(), b'\x807950')
        self.assertEqual(r.struct(), b'\x00\x806001')
        self.assertEqual(r.struct(), b'')
        self.assertEqual(r.struct(), b'')
        bprot = g.Reader(r.struct(), 0)
        self.assertEqual(bprot.take('<HHH'), (1, 1, 0))

    def test_long_struct(self):
        data = bytes(300)
        self.assertEqual(g.Reader(g.cstruct(data), 0).struct(), data)

    def test_data_b3_ind_on_64_bit(self):
        payload = bytes(range(160))
        head = struct.pack('<IIHHH', 0x10103, 0, len(payload), 9, 0) + bytes(8)
        raw = struct.pack('<HHBBH', 8 + len(head), 1, g.DATA_B3, g.IND, 5) + head + payload
        self.assertEqual(g.CapiMessage.parse(raw).data_b3(), (0x10103, 9, payload))

    def test_bit_reverse(self):
        self.assertEqual(g.BIT_REVERSE[0x01], 0x80)
        self.assertEqual(bytes([0xD5]).translate(g.BIT_REVERSE), b'\xab')


class SipHelpersTest(unittest.TestCase):
    def test_rfc2617_digest(self):
        value = g.digest_authorization(
            {'realm': 'testrealm@host.com', 'nonce': 'dcd98b7102dd2f0e8b11d0f600bfb0c093',
             'qop': 'auth,auth-int', 'opaque': '5ccc069c403ebaf9f0171e9517f40e41'},
            'GET', '/dir/index.html', 'Mufasa', 'Circle Of Life', cnonce='0a4f113b')
        self.assertIn('response="6629fae49393a05397450978507c4ef1"', value)

    def test_parse_sdp(self):
        self.assertEqual(g.parse_sdp(sdp(40000)), ('127.0.0.1', 40000, [8]))

    def test_isdn_cause_mapping(self):
        self.assertEqual(g.isdn_reason_to_sip(0x3491), 486)
        self.assertEqual(g.isdn_reason_to_sip(0x3301), 503)


class JitterBufferTest(unittest.TestCase):
    def test_prefill_underrun_and_overflow(self):
        jb = g.JitterBuffer(prefill=4, limit=8)
        self.assertEqual(jb.take(2), b'\xd5\xd5')
        jb.put(b'abc')
        self.assertFalse(jb.playing)
        jb.put(b'd')
        self.assertEqual(jb.take(3), b'abc')
        self.assertEqual(jb.take(3), b'd\xd5\xd5')
        self.assertEqual(jb.underruns, 1)
        jb.put(bytes(9))
        self.assertEqual((len(jb.data), jb.overflows), (4, 1))


class SipToIsdnTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness(isdn_number='7950')
        self.media = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.media.bind(('127.0.0.1', 0))
        self.media.settimeout(2)

    def tearDown(self):
        self.media.close()
        self.h.close()

    def test_call_answer_media_and_bye(self):
        h = self.h
        h.sip_in(invite(self.media.getsockname()[1]))
        self.assertEqual(h.sip_out().status, 100)
        req = h.capi_out()
        self.assertEqual((req.command, req.sub), (g.CONNECT, g.REQ))
        r = req.reader()
        self.assertEqual(r.take('<IH'), (3, 4))
        self.assertEqual(g.called_digits(r.struct()), '7950')
        self.assertEqual(g.calling_digits(r.struct()), '6001')

        plci = 0x0103
        h.capi_in(indication(g.CONNECT, g.CONF, struct.pack('<IH', plci, 0), req.number))
        h.capi_in(indication(g.INFO, g.IND, struct.pack('<IH', plci, g.INFO_ALERTING) + g.cstruct()))
        self.assertEqual(h.capi_out().command, g.INFO)          # INFO_RESP
        self.assertEqual(h.sip_out().status, 180)
        h.capi_in(indication(g.CONNECT_ACTIVE, g.IND, struct.pack('<I', plci)))
        self.assertEqual((h.capi_out().command, h.capi_out().command),
                         (g.CONNECT_ACTIVE, g.CONNECT_B3))
        ncci = 0x10000 | plci
        h.capi_in(indication(g.CONNECT_B3_ACTIVE, g.IND, struct.pack('<I', ncci)))
        self.assertEqual(h.capi_out().command, g.CONNECT_B3_ACTIVE)
        primed = [h.capi_out() for _ in range(2)]
        self.assertTrue(all((m.command, m.sub) == (g.DATA_B3, g.REQ) for m in primed))
        ok = h.sip_out()
        self.assertEqual(ok.status, 200)
        rtp_port = g.parse_sdp(ok.body)[1]
        h.sip_in(f'ACK sip:2910@127.0.0.1 SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKack\r\n'
                 f'From: {ok.get("from")}\r\nTo: {ok.get("to")}\r\nCall-ID: call-1\r\n'
                 'CSeq: 10 ACK\r\nContent-Length: 0\r\n\r\n')
        call = h.gw.calls['call-1']
        self.assertTrue(call.acked)

        # Line to RTP: one 160-octet block becomes one PCMA packet, bit-reversed.
        line = bytes(range(160))
        head = struct.pack('<IIHHH', ncci, 0, 160, 1, 0)
        raw = struct.pack('<HHBBH', 8 + len(head), 1, g.DATA_B3, g.IND, 3) + head + line
        for _ in range(2):                  # two credits outstanding from priming
            h.capi_in(indication(g.DATA_B3, g.CONF, struct.pack('<IHH', ncci, 1, 0)))
        self.media.sendto(b'\x80\x08' + bytes(10) + bytes([0x55]) * 320,
                          ('127.0.0.1', rtp_port))
        select.select([call.rtp], [], [], 2)
        h.gw.on_rtp(call)
        h.capi_in(raw)
        self.assertEqual(h.capi_out().command, g.DATA_B3)       # DATA_B3_RESP
        packet = self.media.recv(2048)
        self.assertEqual(packet[1] & 0x7F, 8)
        self.assertEqual(packet[12:], line.translate(g.BIT_REVERSE))
        sent = h.capi_out()
        self.assertEqual(len(sent.raw), g.DATA_B3_REQ_LEN + 160)
        self.assertEqual(sent.raw[g.DATA_B3_REQ_LEN:], bytes([0x55]).translate(g.BIT_REVERSE) * 160)

        h.sip_in('BYE sip:2910@127.0.0.1 SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKbye\r\n'
                 f'From: {ok.get("from")}\r\nTo: {ok.get("to")}\r\nCall-ID: call-1\r\n'
                 'CSeq: 11 BYE\r\nContent-Length: 0\r\n\r\n')
        self.assertEqual(h.sip_out().status, 200)
        self.assertEqual(h.capi_out().command, g.DISCONNECT)
        self.assertEqual(call.state, 'gone')

    def test_busy_isdn_side_gives_486(self):
        h = self.h
        h.sip_in(invite(self.media.getsockname()[1], 'sip:5551234@127.0.0.1'))
        h.sip_out()
        req = h.capi_out()
        self.assertEqual(g.called_digits(g.Reader(req.raw, 14).struct()), '5551234')
        h.capi_in(indication(g.CONNECT, g.CONF, struct.pack('<IH', 0x0103, 0), req.number))
        h.capi_in(indication(g.DISCONNECT, g.IND, struct.pack('<IH', 0x0103, 0x3491)))
        self.assertEqual(h.capi_out().command, g.DISCONNECT)    # DISCONNECT_RESP
        self.assertEqual(h.sip_out().status, 486)


class IsdnToSipTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.gw.registered = True
        self.media = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.media.bind(('127.0.0.1', 0))

    def tearDown(self):
        self.media.close()
        self.h.close()

    def test_incoming_call_with_auth_then_isdn_hangup(self):
        h, plci = self.h, 0x0203
        body = struct.pack('<IH', plci, 4) + g.cstruct(b'\x806001') + g.cstruct(b'\x00\x80123')
        h.capi_in(indication(g.CONNECT, g.IND, body, number=21))
        alert = h.capi_out()
        self.assertEqual((alert.command, alert.sub), (g.ALERT, g.REQ))
        inv = h.sip_out()
        self.assertEqual(inv.first, 'INVITE sip:6001@127.0.0.1:%d SIP/2.0' % h.asterisk.getsockname()[1])
        self.assertIn('sip:123@', inv.get('from'))

        def reply(request, code, reason, extra='', body=b''):
            to = request.get('to') + (';tag=ast1' if code != 401 else ';tag=ch')
            head = (f'SIP/2.0 {code} {reason}\r\nVia: {request.get("via")}\r\n'
                    f'From: {request.get("from")}\r\nTo: {to}\r\nCall-ID: {request.get("call-id")}\r\n'
                    f'CSeq: {request.get("cseq")}\r\n{extra}')
            if body:
                head += 'Content-Type: application/sdp\r\n'
            h.sip_in(head.encode() + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)

        reply(inv, 401, 'Unauthorized', 'WWW-Authenticate: Digest realm="asterisk",nonce="n1",qop="auth"\r\n')
        self.assertEqual(h.sip_out().method, 'ACK')
        inv2 = h.sip_out()
        self.assertEqual(inv2.cseq, inv.cseq + 1)
        self.assertIn('username="2910"', inv2.get('authorization'))
        reply(inv2, 200, 'OK', 'Contact: <sip:6001@127.0.0.1:5060>\r\n', sdp(self.media.getsockname()[1]))
        self.assertEqual(h.sip_out().method, 'ACK')
        resp = h.capi_out()
        self.assertEqual((resp.command, resp.sub, resp.number), (g.CONNECT, g.RESP, 21))
        self.assertEqual(g.Reader(resp.raw).take('<IH'), (plci, 0))

        h.capi_in(indication(g.CONNECT_ACTIVE, g.IND, struct.pack('<I', plci)))
        h.capi_out()
        h.capi_in(indication(g.CONNECT_B3, g.IND, struct.pack('<I', 0x10000 | plci) + g.cstruct()))
        b3 = h.capi_out()
        self.assertEqual((b3.command, b3.sub), (g.CONNECT_B3, g.RESP))
        h.capi_in(indication(g.CONNECT_B3_ACTIVE, g.IND, struct.pack('<I', 0x10000 | plci)))
        h.capi_out()
        call = h.gw.by_plci[plci]
        self.assertEqual(call.state, 'active')

        h.capi_in(indication(g.DISCONNECT, g.IND, struct.pack('<IH', plci, 0x3490)))
        h.capi_out()
        bye = h.sip_out()
        while bye.method != 'BYE':
            bye = h.sip_out()
        self.assertIn('tag=ast1', bye.get('to'))
        self.assertEqual(call.state, 'gone')

    def test_unregistered_rejects(self):
        h = self.h
        h.gw.registered = False
        body = struct.pack('<IH', 0x0203, 4) + g.cstruct(b'\x806001') + g.cstruct()
        h.capi_in(indication(g.CONNECT, g.IND, body))
        resp = h.capi_out()
        self.assertEqual(g.Reader(resp.raw).take('<IH'), (0x0203, g.REJECT_NORMAL))


if __name__ == '__main__':
    unittest.main()
