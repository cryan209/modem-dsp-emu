import os
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from v90_engine_frame_adapter import (DigitalPhase3ProcessEngine,
                                      Phase3ProcessEngine)


class DigitalPhase3ResetTest(unittest.TestCase):
    def setUp(self):
        self.engine = DigitalPhase3ProcessEngine.__new__(
            DigitalPhase3ProcessEngine)

    def test_default_retains_pre_gate_history(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Phase3ProcessEngine, 'reset') as reset:
            self.engine.reset()
        reset.assert_not_called()

    def test_opt_in_resets_at_the_activation_gate(self):
        with mock.patch.dict(
                os.environ, {'EICON_V90D_PHASE3_RESET_AT_GATE': '1'},
                clear=True), \
                mock.patch.object(Phase3ProcessEngine, 'reset') as reset:
            self.engine.reset()
        reset.assert_called_once_with()


class Phase3DataFramingTest(unittest.TestCase):
    def setUp(self):
        self.engine = Phase3ProcessEngine.__new__(Phase3ProcessEngine)
        self.engine.data_link = mock.Mock()
        self.engine._data_ready = True
        self.engine._last_consumed = 0
        self.engine._data_tx_capture = None

    def test_zero_consumption_does_not_invent_a_bit(self):
        request = self.engine._request(bytes(160))
        self.engine.data_link.take.assert_not_called()
        self.assertEqual(request[160:164], bytes(4))

    def test_reset_clears_data_ready_state(self):
        self.engine.reset_path = mock.Mock()
        self.engine._status_reported = True
        self.engine.reset()
        self.assertFalse(self.engine._data_ready)
        self.assertEqual(self.engine._last_consumed, 2048)
        self.assertFalse(self.engine._status_reported)
        self.engine.reset_path.write_text.assert_called_once_with('reset\n')


if __name__ == '__main__':
    unittest.main()
