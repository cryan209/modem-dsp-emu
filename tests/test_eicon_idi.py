"""Byte-exact expectations for the IDI payloads, against the driver source.

Every expected value here is derived from divas4linux, not from what this
project happens to emit: `putcai()` (tty_module/isdn.c:1209) for the CAI
layout, `atPlusMS()` (tty_module/atp.c:1879) for the modulation masks, and
`assign_nl()` (isdn.c:1425) for the LLI/LLC/DLC block.  A test that only
pinned current behaviour would not have caught the three CAI errors Session
89 found.
"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import eicon_idi as idi


class ParameterFramingTests(unittest.TestCase):
    def test_triples_and_terminator(self):
        payload = idi.idi_parameters((idi.IDI_CAI, b'\x11\x22'),
                                     (idi.IDI_UID, b'Capi20'))
        self.assertEqual(payload.hex(), '100211222d0643617069323000')

    def test_round_trip(self):
        params = [(idi.IDI_CAI, b'\x41'), (idi.IDI_LLC, b'\x09\x04')]
        self.assertEqual(idi.parse_idi_parameters(idi.idi_parameters(*params)),
                         params)

    def test_truncated_ie_is_rejected(self):
        with self.assertRaises(ValueError):
            idi.parse_idi_parameters(bytes((idi.IDI_CAI, 4, 1, 2)))

    def test_oversized_ie_is_rejected(self):
        with self.assertRaises(ValueError):
            idi.idi_parameters((idi.IDI_CAI, b'x' * 256))


class ModulationSelectionTests(unittest.TestCase):
    def test_parse_modulation_defaults_and_rate_fields(self):
        self.assertEqual(idi.parse_modulation('v34'), idi.select_modulation('v34'))
        self.assertEqual(idi.parse_modulation(' v34,0,,33600,,33600 '),
                         idi.select_modulation('v34', automode=0,
                                               max_rx=33600, max_tx=33600))
        for argument in ('', 'v34,0,0,0,0,0,0', 'v34,2', 'v34,0,,56000'):
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                idi.parse_modulation(argument)

    def test_unused_modulations_covers_vfc_k56flex_x2(self):
        # atPlusMS ORs ~(every disable bit the table names) into any non-empty
        # mask, so V.FC, K56flex and X2 -- which no row names -- are disabled
        # whenever a modulation is selected at all.
        for bit in (idi.DSP_CAI_MODEM_DISABLE_VFC,
                    idi.DSP_CAI_MODEM_DISABLE_K56FLEX,
                    idi.DSP_CAI_MODEM_DISABLE_X2):
            self.assertTrue(idi.UNUSED_MODULATIONS & (bit << 8))
        # ...and does not disable anything the table does name.
        self.assertFalse(idi.UNUSED_MODULATIONS
                         & idi.DSP_CAI_MODEM_DISABLE_V34)

    def test_v34_automode_disables_only_faster(self):
        opts = idi.select_modulation('v34', automode=1)
        self.assertEqual(opts.disabled,
                         idi.DSP_CAI_MODEM_DISABLE_V90
                         | idi.UNUSED_MODULATIONS)
        # V.32bis and below stay available as fallbacks.
        self.assertFalse(opts.disabled & idi.DSP_CAI_MODEM_DISABLE_V32BIS)

    def test_v34_strict_disables_everything_else(self):
        opts = idi.select_modulation('v34', automode=0)
        self.assertTrue(opts.disabled & idi.DSP_CAI_MODEM_DISABLE_V90)
        self.assertTrue(opts.disabled & idi.DSP_CAI_MODEM_DISABLE_V32BIS)
        self.assertTrue(opts.disabled & idi.DSP_CAI_MODEM_DISABLE_V21)
        self.assertFalse(opts.disabled & idi.DSP_CAI_MODEM_DISABLE_V34)

    def test_v90_rows_share_a_disable_bit_so_automode_disables_nothing(self):
        # v90a, v90d and v90 all carry DISABLE_V90, and atPlusMS only ORs in
        # rows whose mask *differs* from the selection.
        self.assertEqual(idi.select_modulation('v90', automode=1).disabled, 0)

    def test_v90a_enables_the_analogue_side_bit(self):
        self.assertEqual(idi.select_modulation('v90a', automode=0).enabled,
                         idi.DSP_CAI_MODEM_ENABLE_V90A)

    def test_longest_prefix_wins(self):
        self.assertEqual(idi.MOD2NORM[idi.find_modulation('v90a')].name,
                         'v90a')
        self.assertEqual(idi.MOD2NORM[idi.find_modulation('v90')].name, 'v90')

    def test_numeric_selection(self):
        self.assertEqual(idi.MOD2NORM[idi.find_modulation(11)].name, 'v34')

    def test_v90_speed_rule(self):
        # diva_check_v90_speed: multiples of 4000 plus the two fractional
        # bands, within 28000..56000.
        for good in (28000, 32000, 56000, 29333, 30666):
            self.assertTrue(idi.check_speed('v90', good, 0), good)
        for bad in (27000, 57600, 31000):
            self.assertFalse(idi.check_speed('v90', bad, 0), bad)

    def test_v90_receive_direction_is_v34_limited(self):
        # The v90 row's rx_map is v34: the digital side receives at V.34
        # rates, so a 56000 ceiling on receive is not a legal selection.
        with self.assertRaises(ValueError):
            idi.select_modulation('v90', automode=1, max_rx=56000)
        idi.select_modulation('v90', automode=1, max_tx=56000, max_rx=33600)

    def test_automode_allows_fallback_speeds(self):
        self.assertTrue(idi.check_speed('v34', 9600, automode=1))
        self.assertTrue(idi.check_speed('v34', 9600, automode=0))
        self.assertFalse(idi.check_speed('v32', 33600, automode=0))
        self.assertFalse(idi.check_speed('v32', 33600, automode=1))

    def test_inverted_window_is_rejected(self):
        with self.assertRaises(ValueError):
            idi.select_modulation('v34', automode=1,
                                  min_tx=33600, max_tx=9600)

    def test_unknown_modulation(self):
        with self.assertRaises(ValueError):
            idi.select_modulation('v99')


class CaiTests(unittest.TestCase):
    def cai(self, *args, **kwargs) -> bytes:
        return idi.build_cai(*args, **kwargs)

    def test_default_matches_what_the_shim_has_been_sending(self):
        # The known-good V.90 path: resource 0x11, a 56000 ceiling in both
        # directions at cai[15]/cai[19], everything else zero.  Adopting this
        # module must not change these bytes.
        expected = bytes.fromhex(
            '1100000000000000000000000000c0da0000c0da000000000000')
        self.assertEqual(self.cai(idi.legacy_modem_options()), expected)

    def test_length_is_26_like_add_b1(self):
        self.assertEqual(len(self.cai(idi.legacy_modem_options())), 26)

    def test_field_offsets(self):
        opts = idi.select_modulation('v34', automode=0,
                                     min_tx=2400, max_tx=33600,
                                     min_rx=4800, max_rx=28800)
        opts.negotiation = idi.DSP_CAI_MODEM_NEGOTIATE_V8
        opts.guard_tone = idi.DSP_CAI_MODEM_GUARD_TONE_1800HZ
        opts.line_taking = idi.DSP_CAI_MODEM_DISABLE_CALLING_TONE
        cai = self.cai(opts)
        # Driver cai[n] is index n-1 here.
        self.assertEqual(cai[0], idi.DSP_CAI_HARDWARE_MODEM_ASYNC)  # cai[1]
        self.assertEqual(cai[6], idi.DSP_CAI_MODEM_DISABLE_CALLING_TONE)
        self.assertEqual(cai[7],
                         idi.DSP_CAI_MODEM_NEGOTIATE_V8
                         | idi.DSP_CAI_MODEM_GUARD_TONE_1800HZ)
        self.assertEqual(int.from_bytes(cai[9:11], 'little'), opts.disabled)
        self.assertEqual(int.from_bytes(cai[12:14], 'little'), 2400)
        self.assertEqual(int.from_bytes(cai[14:16], 'little'), 33600)
        self.assertEqual(int.from_bytes(cai[16:18], 'little'), 4800)
        self.assertEqual(int.from_bytes(cai[18:20], 'little'), 28800)

    def test_answer_tone_and_carrier_timers(self):
        opts = idi.select_modulation('v90', automode=1)
        opts.s7 = 60
        opts.s10 = 20
        cai = self.cai(opts)
        self.assertEqual(cai[24], 60)   # cai[25]
        self.assertEqual(cai[25], 20)   # cai[26]

    def test_reserved_modulation_block_extends_the_descriptor(self):
        opts = idi.select_modulation('v23hdx', automode=0)
        cai = self.cai(opts)
        self.assertEqual(len(cai), 33)
        self.assertEqual(cai[26], 6)    # cai[27]: reserved struct length
        self.assertEqual(int.from_bytes(cai[29:33], 'little'),
                         idi.DIVA_MDM_RESERVED_MODULATION_V23_OFF_HOOK)

    def test_fast_connect_adds_the_v22_fc_bits(self):
        opts = idi.select_modulation('v90', automode=1)
        opts.fast_connect_mode = 2
        cai = self.cai(opts)
        self.assertTrue(cai[11] & idi.DSP_CAI_MODEM_ENABLE_V22FC)
        # Mode 1 also silences both tones in the line-taking byte.
        opts.fast_connect_mode = 1
        self.assertTrue(self.cai(opts)[6]
                        & idi.DSP_CAI_MODEM_DISABLE_ANSWER_TONE)

    def test_framing_byte(self):
        self.assertEqual(idi.framing_cai(8, 'N', 1), 0)
        self.assertEqual(idi.framing_cai(7, 'E', 2),
                         idi.DSP_CAI_ASYNC_CHAR_LENGTH_7
                         | idi.DSP_CAI_ASYNC_PARITY_ENABLE
                         | idi.DSP_CAI_ASYNC_PARITY_EVEN
                         | idi.DSP_CAI_ASYNC_TWO_STOP_BITS)


class AssignPayloadTests(unittest.TestCase):
    def test_sig_assign_carries_the_cai_and_the_capi_user_id(self):
        payload = idi.sig_assign_payload(idi.legacy_modem_options())
        params = dict(idi.parse_idi_parameters(payload))
        self.assertEqual(len(params[idi.IDI_CAI]), 26)
        self.assertEqual(params[idi.IDI_UID], b'Capi20')

    def test_nl_assign_default_disables_v42(self):
        payload = idi.nl_assign_payload(signaling_id=0x41)
        params = dict(idi.parse_idi_parameters(payload))
        self.assertEqual(params[idi.IDI_CAI], b'\x41')
        # B2_V42_in is sent either way; the DLC is what turns it off.
        self.assertEqual(params[idi.IDI_LLC],
                         bytes((idi.B2_V42_IN, idi.B3_XPARENT)))
        self.assertTrue(params[idi.IDI_DLC][-1]
                        & idi.DLC_MODEMPROT_DISABLE_V42_V42BIS)

    def test_nl_assign_with_error_control_omits_the_dlc(self):
        payload = idi.nl_assign_payload(signaling_id=0x41,
                                        error_control=True)
        params = dict(idi.parse_idi_parameters(payload))
        self.assertNotIn(idi.IDI_DLC, params)
        self.assertEqual(params[idi.IDI_LLC],
                         bytes((idi.B2_V42_IN, idi.B3_XPARENT)))

    def test_originating_call_uses_b2_v42_out(self):
        params = dict(idi.parse_idi_parameters(
            idi.nl_assign_payload(answering=False, error_control=True)))
        self.assertEqual(params[idi.IDI_LLC],
                         bytes((idi.B2_V42_OUT, idi.B3_XPARENT)))

    def test_no_signalling_id_means_no_cai(self):
        params = dict(idi.parse_idi_parameters(idi.nl_assign_payload()))
        self.assertNotIn(idi.IDI_CAI, params)

    def test_sdlc_template_selection(self):
        opts = idi.ModemOptions(
            disable_error_control=idi.DLC_MODEMPROT_DISABLE_SDLC)
        short = dict(idi.parse_idi_parameters(
            idi.nl_assign_payload(error_control=True, options=opts)))
        self.assertEqual(len(short[idi.IDI_DLC]), 10)

        opts = idi.ModemOptions(
            disable_error_control=idi.DLC_MODEMPROT_DISABLE_V42_V42BIS)
        full = dict(idi.parse_idi_parameters(
            idi.nl_assign_payload(error_control=True, options=opts)))
        self.assertEqual(len(full[idi.IDI_DLC]), 23)


class CallRequestTests(unittest.TestCase):
    """CALL_REQ, against isdnDial() (tty_module/isdn.c:1952)."""

    def parameters(self, payload):
        return dict(idi.parse_idi_parameters(payload))

    def test_driver_element_order(self):
        payload = idi.call_req_payload('6001', origination='6002',
                                       options=idi.legacy_modem_options())
        codes = [code for code, _ in idi.parse_idi_parameters(payload)]
        self.assertEqual(codes, [idi.IDI_UID, idi.IDI_CAI,
                                 idi.IDI_OAD, idi.IDI_CPN])

    def test_addresses_carry_the_numbering_plan(self):
        params = self.parameters(
            idi.call_req_payload('6001', origination='6002'))
        self.assertEqual(params[idi.IDI_CPN], b'\x816001')
        self.assertEqual(params[idi.IDI_OAD], b'\x816002')

    def test_origination_is_omitted_when_empty(self):
        # putaddr() emits nothing for a zero-length address.
        self.assertNotIn(idi.IDI_OAD,
                         self.parameters(idi.call_req_payload('6001')))

    def test_service_pair_rides_in_codeset_6(self):
        # SHIFT|0x08|6, SIN, length 2, service 2 / additional 3 = "data over
        # modem connection", then the terminator.
        payload = idi.call_req_payload('6001')
        self.assertEqual(payload[-6:],
                         bytes((idi.SHIFT | 0x08 | 6, idi.IDI_SIN, 2, 2, 3, 0)))

    def test_parser_stops_at_the_codeset_shift(self):
        # The service pair is not {code, length, data}; decoding must stop
        # rather than read it as one.
        payload = idi.call_req_payload('6001')
        codes = [code for code, _ in idi.parse_idi_parameters(payload)]
        self.assertNotIn(idi.SHIFT | 0x08 | 6, codes)

    def test_subaddress_when_given(self):
        params = self.parameters(
            idi.call_req_payload('6001', destination_subaddress='42'))
        self.assertEqual(params[idi.IDI_DSA], b'\xff42')

    def test_presentation_octet_is_optional(self):
        without = self.parameters(
            idi.call_req_payload('6001', origination='6002'))
        with_octet = self.parameters(
            idi.call_req_payload('6001', origination='6002',
                                 presentation=0x80))
        self.assertEqual(len(with_octet[idi.IDI_OAD]),
                         len(without[idi.IDI_OAD]) + 1)

    def test_cai_matches_the_assign(self):
        options = idi.legacy_modem_options()
        call = self.parameters(idi.call_req_payload('6001', options=options))
        assign = self.parameters(idi.sig_assign_payload(options))
        self.assertEqual(call[idi.IDI_CAI], assign[idi.IDI_CAI])


class ReturnCodeTests(unittest.TestCase):
    def test_assign_ok_is_distinguished_from_an_acknowledged_rejection(self):
        self.assertEqual(idi.rc_name(idi.ASSIGN_OK), 'ASSIGN_OK')
        self.assertIn('rejected', idi.rc_name(0xE6))

    def test_error_codes_are_named(self):
        self.assertEqual(idi.rc_name(idi.OUT_OF_RESOURCES),
                         'OUT_OF_RESOURCES')
        self.assertEqual(idi.rc_name(idi.WRONG_IE), 'WRONG_IE')

    def test_code_names_are_per_entity(self):
        self.assertEqual(idi.code_name(2, 'nl'), 'N_CONNECT')
        self.assertEqual(idi.code_name(2, 'sig'), 'CALL_IND/LISTEN_REQ')


class CallControlTests(unittest.TestCase):
    def make(self, indications=()):
        self.posted = []
        pending = [list(batch) for batch in indications]

        def post(req, entity_id, channel, payload, reference):
            self.posted.append((req, entity_id, channel, payload, reference))

        def pump():
            batch = pending.pop(0) if pending else []
            return [(idi.RC_OK, 0x41, 0, 0)], batch

        return idi.IdiCallControl(post, pump, log=lambda *a: None)

    def test_call_ind_sets_the_channel_and_state(self):
        ind = idi.Indication(idi.CALL_IND, 0x41, 0x07, 0, b'')
        control = self.make([[ind]])
        control.entities['sig'] = 0x41
        control.listen()
        self.assertEqual(control.call_channel, 0x07)
        self.assertIs(control.state, idi.CallState.INCOMING)

    def test_call_res_echoes_the_call_ind_channel(self):
        ind = idi.Indication(idi.CALL_IND, 0x41, 0x07, 0, b'')
        control = self.make([[ind]])
        control.entities['sig'] = 0x41
        control.listen()
        control.answer(b'\x00')
        req, entity_id, channel, _, _ = self.posted[-1]
        self.assertEqual((req, entity_id, channel), (idi.CALL_RES, 0x41, 0x07))
        self.assertIs(control.state, idi.CallState.CONNECTED)

    def test_calling_number_is_read_from_the_call_ind(self):
        payload = idi.idi_parameters((0x6C, b'\x21' + b'6000'))
        ind = idi.Indication(idi.CALL_IND, 0x41, 0x07, 0, payload)
        control = self.make([[ind]])
        control.entities['sig'] = 0x41
        control.listen()
        self.assertEqual(control.calling_number(), '6000')

    def test_hangup_indication_returns_to_idle(self):
        control = self.make([[idi.Indication(idi.HANGUP, 0x41, 0, 0, b'\x10')]])
        control.entities['sig'] = 0x41
        control.listen()
        self.assertIs(control.state, idi.CallState.IDLE)
        self.assertEqual(control.last_cause, 0x10)

    def test_assign_records_the_allocated_id(self):
        posted = []

        def post(req, entity_id, channel, payload, reference):
            posted.append(req)

        def pump():
            return [(idi.ASSIGN_OK, 0x42, 0, 0)], []

        control = idi.IdiCallControl(post, pump, log=lambda *a: None)
        self.assertEqual(control.assign('sig', b'', idi.DSIG_ID), 0x42)
        self.assertEqual(control.entities['sig'], 0x42)

    def test_assign_rejection_leaves_no_entity(self):
        def post(*args):
            pass

        def pump():
            return [(0xE6, 0x00, 0, 0)], []

        control = idi.IdiCallControl(post, pump, log=lambda *a: None)
        self.assertIsNone(control.assign('sig', b'', idi.DSIG_ID))
        self.assertNotIn('sig', control.entities)

    def test_request_without_an_entity_is_an_error(self):
        control = self.make()
        with self.assertRaises(KeyError):
            control.hangup()


class T30InfoTests(unittest.TestCase):
    """divacapi.h:789 for the layout, isdn.c:1567 for the ASSIGN around it."""

    def test_fixed_size_and_station_id_padding(self):
        info = idi.build_t30_info(station_id='0123')
        # Sixteen fixed bytes plus the full twenty-byte station id field: the
        # driver copies sizeof(*T30Info), so a short id is padded, not cut.
        self.assertEqual(len(info), 36)
        self.assertEqual(info[14], 4)              # station_id_len
        self.assertEqual(info[16:36], b'0123' + b'\0' * 16)

    def test_station_id_is_truncated_to_the_field(self):
        info = idi.build_t30_info(station_id='x' * 30)
        self.assertEqual(len(info), 36)
        self.assertEqual(info[14], 20)
        self.assertEqual(info[16:36], b'x' * 20)

    def test_head_line_follows_the_struct_and_is_counted(self):
        info = idi.build_t30_info(station_id='1', head_line='ACME')
        self.assertEqual(info[15], 4)              # head_line_len
        self.assertEqual(info[36:], b'ACME')
        self.assertEqual(len(info), 40)

    def test_outgoing_zeroes_the_station_id_length(self):
        # isdn.c:1577's "HACK HACK HACK": the length goes to zero on an
        # outgoing assign but the field itself stays populated.
        info = idi.build_t30_info(station_id='0123', outgoing=True)
        self.assertEqual(info[14], 0)
        self.assertEqual(info[16:20], b'0123')

    def test_indication_fields_are_left_for_the_card(self):
        info = idi.build_t30_info(station_id='1', control_bits=0xBEEF)
        for offset in (0, 4, 5, 9, 10):
            self.assertEqual(info[offset], 0, f'offset {offset}')
        self.assertEqual(info[7], 0xEF)            # control_bits_low
        self.assertEqual(info[8], 0xBE)            # control_bits_high

    def test_defaults_are_14400_fine_capi(self):
        info = idi.build_t30_info()
        self.assertEqual(info[1], 6)               # rate_div_2400 -> 14400
        self.assertEqual(info[2], idi.T30_RESOLUTION_R8_0770_OR_200)
        self.assertEqual(info[3], idi.T30_DATA_FORMAT_SFF)
        self.assertEqual(info[6], idi.T30_OPERATING_MODE_CAPI)


class FaxNlAssignTests(unittest.TestCase):
    def parse(self, payload):
        return dict(idi.parse_idi_parameters(payload))

    def test_answering_carries_the_fax_protocol_row(self):
        params = self.parse(idi.fax_nl_assign_payload(signaling_id=0x41))
        self.assertEqual(params[idi.IDI_CAI], b'\x41')
        self.assertEqual(params[idi.IDI_LLI], b'\x31')   # OK_FC|CMA|NO_CANCEL
        self.assertEqual(params[idi.IDI_LLC],
                         bytes((idi.B2_T30_IN, idi.B3_T30)))

    def test_originating_flips_only_the_b2(self):
        params = self.parse(idi.fax_nl_assign_payload(answering=False))
        self.assertEqual(params[idi.IDI_LLC],
                         bytes((idi.B2_T30_OUT, idi.B3_T30)))

    def test_dlc_is_the_bare_default_template(self):
        # dlc_def (isdn.c:1430): the maximum info size and nothing else. The
        # modem path's error-control fields must not appear here.
        params = self.parse(idi.fax_nl_assign_payload())
        self.assertEqual(params[idi.IDI_DLC], b'\x5a\x08')  # 2138, little end
        self.assertEqual(len(params[idi.IDI_DLC]), 2)

    def test_nlc_holds_the_t30_info(self):
        params = self.parse(idi.fax_nl_assign_payload(station_id='5551234'))
        self.assertEqual(params[idi.IDI_NLC],
                         idi.build_t30_info(station_id='5551234'))

    def test_t30_info_and_fields_are_mutually_exclusive(self):
        with self.assertRaises(TypeError):
            idi.fax_nl_assign_payload(t30_info=b'\0' * 36, station_id='1')

    def test_parameter_order_follows_the_driver(self):
        payload = idi.fax_nl_assign_payload(signaling_id=0x41)
        codes = [code for code, _ in idi.parse_idi_parameters(payload)]
        self.assertEqual(codes, [idi.IDI_CAI, idi.IDI_LLI, idi.IDI_LLC,
                                 idi.IDI_DLC, idi.IDI_NLC])


class Class1ReconfigureTests(unittest.TestCase):
    def test_tx_hdlc_v21_is_an_n_udata_reconfigure_request(self):
        message = idi.class1_reconfigure('tx-hdlc', 3)
        kind, delay, code, flags = struct.unpack('<BHHH', message)
        self.assertEqual(kind, idi.CLASS1_RECONFIGURE_REQUEST)
        self.assertEqual(delay, 0)
        self.assertEqual(code, idi.CLASS1_RECONFIGURE_TX_FLAG
                         | idi.CLASS1_RECONFIGURE_HDLC_FLAG | 2)

    def test_v34_fax_answer_bootstrap_matches_diva_fax1(self):
        setup = idi.class1_v34fax_setup()
        kind, flags, control, min_tx, max_tx, min_rx, max_rx, mods = (
            struct.unpack('<B7H', setup))
        self.assertEqual(kind, idi.CLASS1_REQUEST_V34FAX_SETUP)
        self.assertEqual(flags, idi.CLASS1_V34FAX_SETUP_NORMAL_CAPABILITY)
        self.assertEqual((control, min_tx, max_tx, min_rx, max_rx),
                         (0, 0, 33600, 0, 33600))
        self.assertEqual(mods, 0x0F00)

        start = idi.class1_v25_answer_start()
        kind, delay, code, preamble = struct.unpack('<BHHH', start)
        self.assertEqual((kind, delay), (idi.CLASS1_RECONFIGURE_REQUEST, 0))
        self.assertEqual(code, idi.CLASS1_RECONFIGURE_V25
                         | idi.CLASS1_RECONFIGURE_HDLC_FLAG
                         | idi.CLASS1_RECONFIGURE_TX_FLAG)
        self.assertEqual(preamble, idi.CLASS1_V25_HDLC_PREAMBLE_FLAGS)

    def test_v34_fax_call_bootstrap_receives_v25_without_preamble(self):
        start = idi.class1_v25_call_start()
        kind, delay, code, preamble = struct.unpack('<BHHH', start)
        self.assertEqual((kind, delay), (idi.CLASS1_RECONFIGURE_REQUEST, 0))
        self.assertEqual(code, idi.CLASS1_RECONFIGURE_V25
                         | idi.CLASS1_RECONFIGURE_HDLC_FLAG)
        self.assertEqual(preamble, 0)

    def test_rx_data_keeps_both_flags_clear(self):
        _, _, code, _ = struct.unpack('<BHHH',
                                      idi.class1_reconfigure('rx-data', 24))
        self.assertEqual(code, 3)


if __name__ == '__main__':
    unittest.main()
