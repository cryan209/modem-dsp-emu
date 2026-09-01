"""The AT command set, against AT.txt and atp.c's observable behaviour."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import eicon_idi as idi
from eicon_at import ActionKind, AtParser, Mode


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class AtTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.at = AtParser(clock=self.clock)

    def send(self, line: str) -> str:
        out, to_link = self.at.feed(line.encode() + b'\r')
        self.assertEqual(to_link, b'')
        return out.decode()


class BasicCommandTests(AtTestCase):
    def test_bare_at_is_ok(self):
        self.assertIn('OK', self.send('AT'))

    def test_unknown_command_is_an_error(self):
        self.assertIn('ERROR', self.send('ATB9'))

    def test_line_without_the_at_prefix_is_an_error(self):
        self.assertIn('ERROR', self.send('HELLO'))

    def test_echo_off_and_on(self):
        self.send('ATE0')
        self.assertNotIn('ATI', self.send('ATI0'))
        self.send('ATE1')
        self.assertIn('AT', self.send('AT'))

    def test_quiet_suppresses_result_codes(self):
        self.send('ATE0Q1')
        self.assertEqual(self.send('AT'), '')

    def test_numeric_result_codes(self):
        self.send('ATE0V0')
        self.assertEqual(self.send('AT'), '0\r')
        self.assertEqual(self.send('ATB9'), '4\r')

    def test_verbose_framing(self):
        self.assertEqual(self.send('AT'), 'AT\r\n\r\nOK\r\n')

    def test_compatibility_commands_are_accepted(self):
        for command in ('ATL2', 'ATM1', 'ATN0', 'ATY0'):
            self.assertIn('OK', self.send(command))

    def test_commands_concatenate(self):
        self.send('ATE0V1X0')
        self.assertFalse(self.at.echo)
        self.assertTrue(self.at.verbose)
        self.assertEqual(self.at.progress, 0)

    def test_repeat_last_command(self):
        self.send('ATS0=7')
        self.at.registers[0] = 0
        self.send('A/')
        self.assertEqual(self.at.registers[0], 7)

    def test_backspace_edits_the_line(self):
        out, _ = self.at.feed(b'ATX9\x084\r')
        self.assertIn('OK', out.decode())
        self.assertEqual(self.at.progress, 4)

    def test_info_strings(self):
        self.assertIn('eicon-adsp-emu', self.send('ATI1'))
        self.assertIn('ERROR', self.send('ATI99'))


class RegisterTests(AtTestCase):
    def test_set_and_query(self):
        self.send('ATS0=3')
        self.assertIn('003', self.send('ATS0?'))
        self.assertEqual(self.at.registers[0], 3)

    def test_query_with_equals_question(self):
        self.assertIn('043', self.send('ATS2=?'))

    def test_out_of_range_is_an_error(self):
        self.assertIn('ERROR', self.send('ATS0=999'))

    def test_defaults_follow_the_manual(self):
        # AT.txt: S0 defaults to 255 (ignore incoming calls), S2 to 43.
        self.assertEqual(self.at.registers[0], 255)
        self.assertEqual(self.at.registers[2], 43)

    def test_registers_reach_the_cai(self):
        self.send('ATS7=60')
        self.send('ATS10=25')
        self.assertEqual(self.at.options().s7, 60)
        self.assertEqual(self.at.options().s10, 25)

    def test_s27_bit_3_disables_the_answer_tone(self):
        self.send('ATS27=8')
        self.assertTrue(self.at.options().line_taking
                        & idi.DSP_CAI_MODEM_DISABLE_ANSWER_TONE)


class ModulationTests(AtTestCase):
    def test_plus_ie_selects_a_modulation(self):
        self.assertIn('OK', self.send('AT+IE=v34,1,,33600,,33600'))
        options = self.at.options()
        self.assertEqual(options.disabled,
                         idi.DSP_CAI_MODEM_DISABLE_V90
                         | idi.UNUSED_MODULATIONS)
        self.assertEqual(options.max_tx, 33600)

    def test_plus_ms_is_an_alias(self):
        self.send('AT+MS=v34,0')
        self.assertTrue(self.at.options().disabled
                        & idi.DSP_CAI_MODEM_DISABLE_V32BIS)

    def test_query_reports_the_selection(self):
        self.send('AT+IE=v34,1,,33600,,33600')
        self.assertIn('v34,1,0,33600,0,33600', self.send('AT+IE?'))

    def test_illegal_speed_is_an_error(self):
        self.assertIn('ERROR', self.send('AT+IE=v34,0,,56000'))

    def test_unknown_modulation_is_an_error(self):
        self.assertIn('ERROR', self.send('AT+IE=v99'))

    def test_selection_survives_into_a_full_cai(self):
        self.send('AT+IE=v34,1,,33600,,33600')
        cai = idi.build_cai(self.at.options())
        self.assertEqual(int.from_bytes(cai[9:11], 'little'),
                         idi.DSP_CAI_MODEM_DISABLE_V90 | idi.UNUSED_MODULATIONS)

    def test_addresses_and_semicolon_separation(self):
        self.send('AT+IA12;S0=1')
        self.assertEqual(self.at.accepted_address, '12')
        self.assertEqual(self.at.registers[0], 1)

    def test_guard_tone(self):
        self.send('AT&G2')
        self.assertEqual(self.at.options().guard_tone,
                         idi.DSP_CAI_MODEM_GUARD_TONE_1800HZ)
        self.assertIn('ERROR', self.send('AT&G7'))


class FaxClassTests(AtTestCase):
    def test_fclass_reports_and_selects_the_supported_classes(self):
        self.assertIn('0,1,2', self.send('AT+FCLASS=?'))
        self.assertIn('0', self.send('AT+FCLASS?'))
        self.assertIn('OK', self.send('AT+FCLASS=1'))
        self.assertEqual(self.at.fax_class, 1)
        self.assertIn('1', self.send('AT+FCLASS?'))

    def test_reset_returns_to_data_class(self):
        self.send('AT+FCLASS=2')
        self.send('ATZ')
        self.assertEqual(self.at.fax_class, 0)

    def test_unsupported_fax_class_is_an_error(self):
        self.assertIn('ERROR', self.send('AT+FCLASS=2.1'))
        self.assertIn('ERROR', self.send('AT+FCLASS=9'))

    def test_class1_transmit_unescapes_and_queues_a_frame(self):
        self.send('AT+FCLASS=1')
        out, to_link = self.at.feed(b'AT+FTH=3\r')
        self.assertEqual(to_link, b'')
        self.assertIn(b'CONNECT', out)
        self.assertIs(self.at.mode, Mode.FAX_DATA)
        config = self.at.actions[-1]
        self.assertEqual(config.kind, ActionKind.FAX_CONFIG)
        self.assertEqual(config.fax_operation, 'tx-hdlc')
        self.assertEqual(config.fax_modulation, 3)
        out, to_link = self.at.feed(b'one\x10\x10two\x10\x03')
        self.assertEqual(to_link, b'')
        self.assertIn(b'OK', out)
        action = self.at.actions[-1]
        self.assertEqual(action.kind, ActionKind.FAX_SEND)
        self.assertEqual(action.fax_operation, 'tx-hdlc')
        self.assertEqual(action.fax_payload, b'one\x10two')

    def test_class1_receive_escapes_dle_and_completes_the_phase(self):
        self.send('AT+FCLASS=1')
        self.at.feed(b'AT+FRM=24\r')
        self.assertEqual(self.at.fax_receive(b'a\x10b'), b'a\x10\x10b')
        done = self.at.fax_receive(complete=True)
        self.assertEqual(done, b'\x10\x03\r\nOK\r\n')
        self.assertIs(self.at.mode, Mode.COMMAND)

    def test_class1_media_requires_class1(self):
        self.assertIn('ERROR', self.send('AT+FTH=3'))


class ProfileTests(AtTestCase):
    def test_ampersand_f_resets_registers(self):
        self.send('ATS0=9')
        self.send('AT&F5')
        self.assertEqual(self.at.registers[0], 255)

    def test_unknown_profile_is_an_error(self):
        self.assertIn('ERROR', self.send('AT&F99'))
        self.assertIn('ERROR', self.send('ATZ99'))

    def test_ppp_profile_comes_up_numeric_and_echo_off(self):
        # AT.txt note [b] for profile 9.
        self.send('AT&F9')
        self.assertFalse(self.at.verbose)
        self.assertFalse(self.at.echo)

    def test_atz_queues_a_reset_action(self):
        self.send('ATZ5')
        self.assertEqual(self.at.actions[-1].kind, ActionKind.RESET)
        self.assertEqual(self.at.actions[-1].profile, 5)


class CallControlTests(AtTestCase):
    def test_dial_queues_the_number(self):
        self.send('ATDT6001')
        action = self.at.actions[-1]
        self.assertEqual(action.kind, ActionKind.DIAL)
        self.assertEqual(action.number, '6001')

    def test_dial_modifiers_are_dropped(self):
        self.send('ATD 1-800 555,W1212')
        self.assertEqual(self.at.actions[-1].number, '18005551212')

    def test_dial_stops_at_a_semicolon(self):
        self.send('ATD6001;')
        self.assertEqual(self.at.actions[-1].number, '6001')

    def test_dial_carries_the_current_options(self):
        self.send('AT+IE=v34,1')
        self.send('ATD6001')
        self.assertTrue(self.at.actions[-1].options.disabled)

    def test_answer_and_hangup(self):
        self.send('ATA')
        self.assertEqual(self.at.actions[-1].kind, ActionKind.ANSWER)
        self.send('ATH')
        self.assertEqual(self.at.actions[-1].kind, ActionKind.HANGUP)

    def test_ring_without_auto_answer(self):
        self.at.registers[0] = 0
        self.assertIn('RING', self.at.ring('6000').decode())
        self.assertEqual(self.at.actions, [])

    def test_ring_auto_answers_when_s0_is_set(self):
        self.at.registers[0] = 1
        self.at.ring('6000')
        self.assertEqual(self.at.actions[-1].kind, ActionKind.ANSWER)

    def test_ring_is_ignored_when_s0_is_255(self):
        self.assertEqual(self.at.registers[0], 255)
        self.at.ring('6000')
        self.assertEqual(self.at.actions, [])

    def test_connect_switches_to_data_mode(self):
        self.at.connected(38666, 33600, 'V90', 'LAPM', 'NONE')
        self.assertIs(self.at.mode, Mode.DATA)

    def test_connect_detail_formats(self):
        text = self.at.connected(38666, 33600, 'V90', 'LAPM', 'NONE').decode()
        self.assertIn('CONNECT V90/LAPM/38666:TX/33600:RX', text)

    def test_x0_reports_a_plain_connect(self):
        self.send('ATX0')
        self.assertIn('CONNECT', self.at.connected(38666, 33600, 'V90').decode())
        self.assertNotIn('38666',
                         self.at.connected(38666, 33600, 'V90').decode())

    def test_x0_collapses_call_failures_to_no_carrier(self):
        self.send('ATX0')
        self.assertIn('NO CARRIER', self.at.respond('BUSY').decode())
        self.send('ATX4')
        self.assertIn('BUSY', self.at.respond('BUSY').decode())

    def test_no_carrier_returns_to_command_mode(self):
        self.at.connected(38666, 33600)
        self.at.no_carrier()
        self.assertIs(self.at.mode, Mode.COMMAND)

    def test_plus_ic_keeps_command_mode_after_connect(self):
        self.send('AT+IC0')
        self.at.connected(38666, 33600)
        self.assertIs(self.at.mode, Mode.COMMAND)


class DataModeTests(AtTestCase):
    def connect(self):
        self.at.connected(38666, 33600)
        self.clock.advance(5)

    def test_payload_passes_through(self):
        self.connect()
        to_terminal, to_link = self.at.feed(b'hello')
        self.assertEqual((to_terminal, to_link), (b'', b'hello'))

    def test_escape_needs_both_guard_times(self):
        self.connect()
        self.at.feed(b'+++')
        self.assertEqual(self.at.poll(), b'')      # trailing guard not elapsed
        self.assertIs(self.at.mode, Mode.DATA)
        self.clock.advance(2)
        self.assertIn('OK', self.at.poll().decode())
        self.assertIs(self.at.mode, Mode.COMMAND)

    def test_escape_inside_a_stream_is_data(self):
        self.connect()
        _, to_link = self.at.feed(b'data+++more')
        self.assertEqual(to_link, b'data+++more')
        self.clock.advance(2)
        self.assertEqual(self.at.poll(), b'')
        self.assertIs(self.at.mode, Mode.DATA)

    def test_escape_without_a_leading_guard_is_data(self):
        self.at.connected(38666, 33600)   # no advance: line is "busy"
        _, to_link = self.at.feed(b'+++')
        self.assertEqual(to_link, b'+++')
        self.assertIs(self.at.mode, Mode.DATA)

    def test_s2_above_127_disables_the_escape(self):
        self.connect()
        self.at.registers[2] = 128
        _, to_link = self.at.feed(b'+++')
        self.assertEqual(to_link, b'+++')
        self.clock.advance(2)
        self.assertEqual(self.at.poll(), b'')

    def test_ato_returns_to_data_mode(self):
        self.connect()
        self.at.feed(b'+++')
        self.clock.advance(2)
        self.at.poll()
        self.send('ATO')
        self.assertIs(self.at.mode, Mode.DATA)
        self.assertEqual(self.at.actions[-1].kind, ActionKind.ONLINE)


if __name__ == '__main__':
    unittest.main()
