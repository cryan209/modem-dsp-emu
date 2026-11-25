"""Modulation prerequisites must follow the effective endpoint selection."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_loopback import end_environment, transmit_buffer_ms, receive_hold_ms


class LoopbackEnvironmentTests(unittest.TestCase):
    def test_v34_receive_hold_and_explicit_override(self):
        self.assertEqual(receive_hold_ms(None, 'v34,0'), 500)
        for selection in ('', 'v90', 'v90a', 'v32b'):
            self.assertEqual(receive_hold_ms(None, selection), 60)
        for requested in (0, 60, 500, 1000):
            self.assertEqual(receive_hold_ms(requested, 'v34,0'), requested)

    def test_v34_latency_default_and_explicit_buffer_precedence(self):
        self.assertEqual(transmit_buffer_ms(None, 'v34,0'), 20)
        self.assertEqual(transmit_buffer_ms(None, 'v34,0,33600,33600,33600,33600'), 20)
        for selection in ('', 'v90', 'v90a', 'v22b,0'):
            self.assertEqual(transmit_buffer_ms(None, selection), 160)
        for requested in (0, 20, 160, 300):
            self.assertEqual(transmit_buffer_ms(requested, 'v34,0'), requested)

    def test_global_and_endpoint_modulation_have_identical_prerequisites(self):
        for selection in ('v34,0,9600,9600,9600,9600', 'v90', 'v90a'):
            with self.subTest(selection=selection):
                global_env = end_environment({'EICON_MODULATION': selection},
                                             None, 'pri117', 'answerer')
                endpoint_env = end_environment({}, selection, 'pri117', 'answerer')
                self.assertEqual(global_env, endpoint_env)
                if selection == 'v90a':
                    self.assertEqual(global_env['EICON_DSP_EXTRA_DOWNLOADS'], '0x026b')
                else:
                    self.assertEqual(global_env['EICON_EXPAND_SPORT'], '1')

    def test_endpoint_selection_and_explicit_sport_override_take_precedence(self):
        env = end_environment({'EICON_MODULATION': 'v90a'}, 'v34',
                              'pri117', 'answerer', ['EICON_EXPAND_SPORT=0'])
        self.assertEqual(env['EICON_MODULATION'], 'v34')
        self.assertEqual(env['EICON_EXPAND_SPORT'], '0')
        self.assertNotIn('EICON_DSP_EXTRA_DOWNLOADS', env)

    def test_analog_endpoint_does_not_get_pri_prerequisites(self):
        env = end_environment({'EICON_MODULATION': 'v90a'}, None,
                              'analog109', 'caller')
        self.assertNotIn('EICON_EXPAND_SPORT', env)
        self.assertNotIn('EICON_DSP_EXTRA_DOWNLOADS', env)
