"""The BYE that ends a call this endpoint answered.

A UAS BYE is not the UAC one with the addresses swapped by accident: it has to
reuse the dialog the INVITE established, with our tag on the From and the
caller's on the To, sent to the Contact rather than to the AOR. Getting it
wrong produces a BYE the switch ignores, which looks exactly like sending no
BYE at all -- and the symptom of sending none is a later call to the same
extension coming back BUSY, several layers away from the cause.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))

from eicon_adsp_sip import build_inbound_bye, contact_uri


class ContactUriTests(unittest.TestCase):

    def test_angle_brackets_win_over_header_parameters(self):
        # ;expires belongs to the header, not the URI: splitting on ';' first
        # would work here by luck and fail when the URI carries its own.
        self.assertEqual(
            contact_uri('<sip:8405@192.168.88.122:5060>;expires=120', 'fb'),
            'sip:8405@192.168.88.122:5060')

    def test_a_uri_parameter_is_kept(self):
        self.assertEqual(
            contact_uri('<sip:8405@192.168.88.122;transport=udp>', 'fb'),
            'sip:8405@192.168.88.122;transport=udp')

    def test_a_bare_uri_is_accepted(self):
        self.assertEqual(contact_uri('sip:8405@192.168.88.122', 'fb'),
                         'sip:8405@192.168.88.122')

    def test_an_absent_or_junk_contact_falls_back(self):
        self.assertEqual(contact_uri('', 'sip:192.168.88.122:5060'),
                         'sip:192.168.88.122:5060')
        self.assertEqual(contact_uri('*', 'sip:192.168.88.122:5060'),
                         'sip:192.168.88.122:5060')


class InboundByeTests(unittest.TestCase):

    def build(self, **over):
        args = dict(
            target='sip:8405@192.168.88.122:5060',
            via_host='192.168.88.167', via_port=5060, branch='abc123',
            from_header='<sip:6001@asterisk.net.cryan.nz>',
            local_tag='deadbeef',
            to_header='"VG224 Port 2/5" <sip:8405@192.168.88.122>;tag=b19c1a85',
            call_id='call-id-1', cseq=2)
        args.update(over)
        return build_inbound_bye(**args)

    def test_the_request_goes_to_the_remote_target(self):
        self.assertTrue(
            self.build().startswith('BYE sip:8405@192.168.88.122:5060 SIP/2.0'))

    def test_our_tag_is_added_to_the_from(self):
        # The INVITE's To had no tag -- we chose it when we answered -- so the
        # BYE has to carry it or the dialog will not match.
        line = [l for l in self.build().split('\r\n') if l.startswith('From:')]
        self.assertEqual(line, ['From: <sip:6001@asterisk.net.cryan.nz>;tag=deadbeef'])

    def test_an_existing_from_tag_is_not_doubled(self):
        msg = self.build(from_header='<sip:6001@x>;tag=already')
        line = [l for l in msg.split('\r\n') if l.startswith('From:')][0]
        self.assertEqual(line, 'From: <sip:6001@x>;tag=already')
        self.assertEqual(line.count('tag='), 1)

    def test_the_caller_keeps_its_own_tag_on_the_to(self):
        line = [l for l in self.build().split('\r\n') if l.startswith('To:')][0]
        self.assertIn('tag=b19c1a85', line)
        self.assertIn('sip:8405@192.168.88.122', line)

    def test_the_dialog_identifiers_are_carried(self):
        msg = self.build()
        self.assertIn('Call-ID: call-id-1\r\n', msg)
        self.assertIn('CSeq: 2 BYE\r\n', msg)

    def test_the_branch_is_rfc3261_magic_cookied(self):
        via = [l for l in self.build().split('\r\n') if l.startswith('Via:')][0]
        self.assertIn('branch=z9hG4bKabc123', via)
        self.assertIn('192.168.88.167:5060', via)

    def test_it_is_a_well_formed_message(self):
        msg = self.build()
        self.assertTrue(msg.endswith('\r\n\r\n'))
        self.assertIn('Content-Length: 0', msg)
        self.assertIn('Max-Forwards: 70', msg)
        # No header may be empty, which is what an absent dialog field would
        # produce and what a switch would silently drop.
        for line in msg.split('\r\n')[1:]:
            if line:
                self.assertTrue(re.match(r'^[A-Za-z-]+: .+', line), line)


if __name__ == '__main__':
    unittest.main()
