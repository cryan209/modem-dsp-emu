"""ISDN setup probes must detect misrouting without claiming payload success."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import eicon_idi as idi
import eicon_isdn_profile_probe as probe
import eicon_mips_shim as shim


class IsdnProfileProbeTest(unittest.TestCase):
    def test_expected_native_stacks_are_accepted_by_the_comparator(self):
        for profile in probe.NATIVE_PROFILES:
            with self.subTest(profile=profile):
                b1, answer_b2, caller_b2, b3 = probe.EXPECTED_STACKS[profile]
                self.assertEqual(probe.compare_stack(
                    (b1, answer_b2, caller_b2, b3), b1,
                    bytes((answer_b2, b3)), bytes((caller_b2, b3))), [])

    def test_modem_stack_is_detected_as_misrouting_for_every_native_profile(self):
        for profile in probe.NATIVE_PROFILES:
            with self.subTest(profile=profile):
                differences = probe.compare_stack(
                    probe.EXPECTED_STACKS[profile], 0x11, b'\x09\x04', b'\x0a\x04')
                self.assertEqual(len(differences), 3)
                self.assertTrue(differences[0].startswith('B1:'))

    def test_wrong_calling_layer_three_is_not_hidden_by_correct_answer(self):
        self.assertEqual(probe.compare_stack(
            probe.EXPECTED_STACKS[9], 0x05, b'\x03\x04', b'\x02\x06'),
            ['B3 caller: expected 0x04, got 0x06'])

    def test_actual_endpoint_handoff_and_descriptors_are_recorded(self):
        previous = shim._MODEM_OPTIONS_OVERRIDE
        for profile in (*probe.NATIVE_PROFILES, 5):
            with self.subTest(profile=profile):
                result = probe.probe_profile(profile)
                self.assertTrue(result['profile_accepted'])
                self.assertTrue(result['configuration_matches'])
                sig = dict(idi.parse_idi_parameters(bytes.fromhex(result['sig_assign_hex'])))
                self.assertEqual(sig[idi.IDI_CAI].hex(), result['cai_hex'])
                self.assertEqual(sig[idi.IDI_CAI][0], result['actual_stack'][0])
                self.assertFalse(result['payload_transfer_verified'])
                self.assertEqual(result['configuration_matches'], not result['differences'])
                self.assertIs(shim._MODEM_OPTIONS_OVERRIDE, previous)

    def test_switching_back_to_modem_clears_native_selection(self):
        shim.set_isdn_options(idi.IsdnOptions(9))
        try:
            shim.set_modem_options(idi.legacy_modem_options())
            self.assertIsNone(shim._ISDN_OPTIONS_OVERRIDE)
            self.assertEqual(shim.modem_cai()[0], 0x11)
        finally:
            shim.set_modem_options(None)

    def test_native_outgoing_call_uses_same_cai_as_assignment(self):
        for profile in probe.NATIVE_PROFILES:
            result = probe.probe_profile(profile)
            # CALL_REQ contains a codeset-6 service tail after ordinary IEs.
            payload = bytes.fromhex(result['call_req_hex'])
            parameters = dict(idi.parse_idi_parameters(payload[:-6] + b'\0'))
            self.assertEqual(parameters[idi.IDI_CAI].hex(), result['cai_hex'])
            self.assertEqual(payload[-4:-1], bytes((2, 1 if profile == 8 else 7,
                                                   170 if profile in (7, 10) else 0)))

    def test_native_profile_cannot_silently_return_modem_options(self):
        from eicon_at import AtParser
        at = AtParser()
        at.feed(b'AT&F3+IB5\r')
        self.assertEqual(at.isdn_options().user_rate, 9600)
        self.assertEqual(idi.isdn_cai(at.isdn_options()), bytes.fromhex('0d 05 00 00 00 08'))
        with self.assertRaises(ValueError):
            at.options()
        at.feed(b'AT&F5\r')
        self.assertIsNone(at.isdn_options())
        self.assertEqual(idi.build_cai(at.options())[0], 0x11)

    def test_native_dial_answer_and_ring_actions_carry_native_options(self):
        from eicon_at import AtParser, ActionKind
        for profile in probe.NATIVE_PROFILES:
            at = AtParser()
            at.feed(f'AT&F{profile}D6001\r'.encode())
            self.assertEqual(at.actions[-1].kind, ActionKind.DIAL)
            self.assertEqual(at.actions[-1].options.profile, profile)
            at.feed(b'ATA\r')
            self.assertEqual(at.actions[-1].kind, ActionKind.ANSWER)
            at.registers[0] = 1
            at.ring('peer')
            self.assertEqual(at.actions[-1].options.profile, profile)

    def test_native_dial_cannot_accidentally_start_a_g711_modem(self):
        from eicon_at import AtParser
        from eicon_adsp_sip import EiconSipEndpoint
        endpoint = EiconSipEndpoint.__new__(EiconSipEndpoint)
        endpoint.at = AtParser()
        endpoint.at.feed(b'AT&F9\r')
        self.assertFalse(endpoint.dial('6001', '127.0.0.1:5070'))

    def test_known_modem_control_matches_the_modem_configuration(self):
        result = probe.probe_profile(5)
        self.assertTrue(result['configuration_matches'])
        self.assertFalse(result['payload_transfer_verified'])


if __name__ == '__main__':
    unittest.main()
