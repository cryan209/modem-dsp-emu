"""Delayed direct-backend watches must not load MIPS configuration."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_adsp_sip import EiconSipEndpoint


class DmAssertionBackendTests(unittest.TestCase):
    def test_direct_watch_does_not_import_native_backend(self):
        endpoint = SimpleNamespace(assert_dm_clean=(0x1DCB, 0x1DCD, 0x026B, 16),
                                   assert_dm_armed=False)
        core = Mock()
        cpu = object()
        # The direct backend's colon-separated EICON_PATCH_PM is incompatible
        # with MIPS's parser. Importing that backend here can kill a live call.
        with patch.dict(sys.modules, {'eicon_mips_shim': None}), \
                patch('eicon_adsp_sip.ADSP', core):
            EiconSipEndpoint._arm_dm_assertion(endpoint, cpu)
        self.assertTrue(endpoint.assert_dm_armed)
        self.assertEqual(core.adsp2181_watch_dm_writes.call_args_list,
                         [call(cpu, address, 16)
                          for address in range(0x1DCB, 0x1DCE)])


if __name__ == '__main__':
    unittest.main()
