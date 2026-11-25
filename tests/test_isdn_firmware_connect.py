"""Regress the native network boundary without executing a MIPS JIT."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import eicon_mips_shim as fw
import eicon_idi as idi


class BearerGateTests(unittest.TestCase):
    def test_disconnect_closes_previously_accepted_bearer(self):
        shim = SimpleNamespace(nl_connected=True)
        fw.observe_nl_bearer(shim, 3, idi.N_DISC, 3)
        self.assertFalse(shim.nl_connected)

    def test_other_entity_cannot_change_gate(self):
        shim = SimpleNamespace(nl_connected=True)
        fw.observe_nl_bearer(shim, 3, idi.N_DISC, 2)
        self.assertTrue(shim.nl_connected)
        shim.nl_connected = False
        fw.observe_nl_bearer(shim, 3, idi.N_CONNECT_ACK, 2)
        self.assertFalse(shim.nl_connected)

    def test_asynchronous_connect_confirmation_opens_gate(self):
        shim = SimpleNamespace(nl_connected=False)
        fw.observe_nl_bearer(shim, 3, idi.N_CONNECT_ACK, 3)
        self.assertTrue(shim.nl_connected)


class NetworkBoundaryTests(unittest.TestCase):
    def test_native_connect_uses_q931_and_firmware_message_queue(self):
        calls, writes = [], []
        pointers = {fw.ENTITY_TABLE: 0x1000, 0x101c: 0x2000,
                    0x80121674: 0x3000, 0x3004: 0x4000,
                    0x2024: 0x5000, 0x5028: 0x6000}
        def call(address, args, **kwargs):
            calls.append((address, args))
            return 0x7000 if address == 0x800677cc else 0
        shim = SimpleNamespace(call=call, phase='',
            write8=lambda a, v: writes.append((a, bytes([v]))),
            write16=lambda a, v: writes.append((a, v.to_bytes(2, 'little'))),
            write_bytes=lambda a, v: writes.append((a, v)),
            uc=SimpleNamespace(mem_read=lambda a, n: b'\x0a'))
        with patch.object(fw, 'read_runtime32', side_effect=lambda s, a: pointers.get(a, 0)):
            fw.inject_isdn_network_connect(shim, 0x9000, 0xa000)
        self.assertNotIn(fw.CALL_INGRESS_PARSER, [a for a, args in calls])
        self.assertIn((0x8002d768, [0x6000, 2, 0]), calls)
        self.assertIn((0x800673ac, [0x7000, 0x200c]), calls)
        self.assertIn((0x700c, bytes.fromhex('080180071803a18382')), writes)
        self.assertFalse(any(a == 0x202c for a, value in writes))

    def test_d_channel_cannot_be_selected_as_bearer(self):
        with self.assertRaises(ValueError):
            fw.inject_isdn_network_connect(None, 0, 0, channel=16)


if __name__ == '__main__':
    unittest.main()
