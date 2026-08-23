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


if __name__ == '__main__':
    unittest.main()
