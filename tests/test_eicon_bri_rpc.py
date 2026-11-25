"""Exercise the real board over the frontend's framed byte stream."""
import socket
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from eicon_bri_rpc import (BriPciBackend, serve_connection, receive_exact,
    MAGIC, VERSION, REQUEST, REPLY, HELLO, READ, WRITE, ADVANCE,
    CONFIG_READ, CONFIG_WRITE, RESET)

ROOT = Path(__file__).resolve().parents[1]


class BriRpcTest(unittest.TestCase):
    def setUp(self):
        self.backend = BriPciBackend()
        self.client, self.peer = socket.socketpair()
        self.errors = []
        def server():
            try:
                serve_connection(self.peer, self.backend)
            except Exception as error:
                self.errors.append(error)
        self.worker = threading.Thread(target=server, daemon=True)
        self.worker.start()
        self.call(HELLO, value=VERSION)

    def tearDown(self):
        self.client.close()
        self.worker.join(2)
        self.peer.close()
        self.backend.close()
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.errors, [])

    def call(self, operation, bar=0, size=0, offset=0, value=0, expected_status=0):
        packet = REQUEST.pack(MAGIC, operation, bar, size, offset, value)
        # Exercise stream fragmentation rather than assuming packet boundaries.
        for start in range(0, len(packet), 3):
            self.client.sendall(packet[start:start + 3])
        reply = REPLY.unpack(receive_exact(self.client, REPLY.size))
        self.assertEqual(reply[0], MAGIC)
        self.assertEqual(reply[1], expected_status)
        self.assertEqual(reply[4], 0)
        return reply[2], reply[3]

    def test_pci_feature_read_bar_memory_irq_ack_and_fault_reset(self):
        self.assertEqual(self.call(CONFIG_READ, size=4)[0], 0xe0131133)
        self.call(CONFIG_WRITE, size=2, offset=0x4e, value=252)
        self.assertEqual(self.call(CONFIG_READ, size=2, offset=0x4e)[0], 0x80fc)
        self.assertEqual(self.call(CONFIG_READ, size=4, offset=0x50)[0], 12196)
        self.call(WRITE, bar=2, size=4, offset=0x100, value=0x12345678)
        self.assertEqual(self.call(READ, bar=2, size=4, offset=0x100)[0], 0x12345678)
        self.call(WRITE, bar=0, size=1, offset=0x69, value=9)
        self.assertEqual(self.call(WRITE, bar=3, size=4, offset=0x400, value=1)[1] & 1, 1)
        self.assertEqual(self.call(WRITE, bar=3, size=4, offset=0x400, value=0)[1] & 1, 0)
        self.call(READ, bar=3, size=4, offset=0x7ffc, expected_status=1)
        self.assertIsNotNone(self.backend.fault)
        self.call(RESET)
        self.assertIsNone(self.backend.fault)
        self.assertEqual(self.call(READ, bar=2, size=4, offset=0x100)[0], 0x12345678)

    def test_power_configuration_sync_and_d3_access_failure_recover_with_reset(self):
        self.call(WRITE, bar=2, size=4, offset=0x80000, value=0x12345678)
        self.call(CONFIG_WRITE, size=2, offset=0x44, value=3)
        self.assertEqual(self.call(CONFIG_READ, size=2, offset=0x44)[0], 3)
        self.assertEqual(self.backend.card.pci.power_transitions, [3])
        self.call(READ, bar=2, size=4, offset=0x80000, expected_status=1)
        self.call(RESET)
        self.assertEqual(self.call(CONFIG_READ, size=2, offset=0x44)[0], 0)
        self.assertEqual(self.backend.card.pci.power_transitions, [3, 0])
        self.assertEqual(self.call(READ, bar=2, size=4, offset=0x80000)[0], 0x12345678)
        self.backend.card.risc = 3
        self.call(CONFIG_WRITE, size=2, offset=0x44, value=3, expected_status=1)
        self.assertEqual(self.backend.card.pci.power_state, 0)
        self.call(RESET)

    def test_pending_interrupt_is_suppressed_in_d3_and_cleared_after_resume(self):
        self.call(WRITE, bar=0, size=1, offset=0x69, value=9)
        self.assertTrue(self.call(WRITE, bar=3, size=4, offset=0x400, value=1)[1] & 1)
        self.assertFalse(self.call(CONFIG_WRITE, size=2, offset=0x44, value=3)[1] & 1)
        self.assertFalse(self.call(CONFIG_READ, size=2, offset=0x44)[1] & 1)
        self.assertFalse(self.call(CONFIG_WRITE, size=2, offset=0x44, value=0)[1] & 1)
        self.assertEqual(self.call(READ, bar=3, size=4, offset=0x400)[0], 0)
        self.assertEqual(self.call(READ, bar=0, size=1, offset=0x69)[0], 9)
        # Source delivery still works normally after resume and acknowledgement.
        self.assertTrue(self.call(WRITE, bar=3, size=4, offset=0x400, value=1)[1] & 1)
        self.assertFalse(self.call(WRITE, bar=3, size=4, offset=0x400, value=0)[1] & 1)

    def test_transport_reset_recovers_interrupted_plx_sequence_without_erasing_ram(self):
        self.call(WRITE, bar=2, size=4, offset=0x80000, value=0x12345678)
        pins = self.call(READ, bar=0, size=2, offset=0x6e)[0]
        flags = self.call(WRITE, bar=0, size=2, offset=0x6e, value=pins | 0x4000)[1]
        self.assertTrue(flags & 8)
        self.assertEqual(self.call(READ, bar=2, size=4, offset=0x80000)[0], 0xffffffff)
        self.assertFalse(self.call(RESET)[1] & 8)
        self.assertEqual(self.call(READ, bar=2, size=4, offset=0x80000)[0], 0x12345678)
        self.assertEqual(self.call(READ, bar=0, size=2, offset=0x6e)[0], pins)

    def test_guest_loaded_mips_restarts_after_reset_without_fabricating_ready(self):
        from unicorn.mips_const import UC_MIPS_REG_V0
        for increment in (1, 5):
            self.call(RESET)
            for offset, word in enumerate((0x24420000 | increment, 0x1000ffff, 0, 0)):
                self.call(WRITE, bar=2, size=4, offset=offset * 4, value=word)
            self.call(WRITE, bar=3, size=4, offset=0x200, value=3)
            self.call(ADVANCE, value=20)
            self.assertEqual(self.backend.machine.uc.reg_read(UC_MIPS_REG_V0), increment)
            self.assertTrue(all(not c['signature'] for c in self.backend.card.snapshot()['controllers']))

    def test_stock_firmware_boots_via_pci_transport_and_virtual_time(self):
        self.backend.card.load_reentrant(ROOT / 'docs/firmware/te_dmlt.2qm',
                                         ROOT / 'docs/firmware/dspdload.bin')
        self.call(WRITE, bar=3, size=4, offset=0x200, value=3)
        for _ in range(20):
            self.call(ADVANCE, value=1000000)
        self.assertIsNone(self.backend.fault)
        self.assertEqual([c['signature'] for c in self.backend.card.snapshot()['controllers']],
                         [0x4447] * 4)
        self.assertEqual(len(self.backend.card.dsps), 8)


class RpcFramingTest(unittest.TestCase):
    def test_shared_sdram_is_coherent_with_guest_mapping_and_mips(self):
        import gc
        import mmap
        import struct
        import tempfile
        from eicon_bri_card import BriCard, MEMORY_SIZE
        from unicorn.mips_const import UC_MIPS_REG_V0
        with tempfile.TemporaryFile() as file:
            file.truncate(MEMORY_SIZE)
            first = mmap.mmap(file.fileno(), MEMORY_SIZE)
            second = mmap.mmap(file.fileno(), MEMORY_SIZE)
            backend = BriPciBackend(BriCard(ram=first))
            try:
                second[0x100:0x104] = b'BRI!'
                self.assertEqual(backend.access(READ, 2, 4, 0x100, 0), 0x21495242)
                backend.access(WRITE, 2, 4, 0x104, 0x12345678)
                self.assertEqual(second[0x104:0x108], bytes.fromhex('78563412'))
                second[:16] = struct.pack('<IIII', 0x24420007, 0x1000ffff, 0, 0)
                backend.access(WRITE, 3, 4, 0x200, 3)
                backend.access(ADVANCE, 0, 0, 0, 20)
                self.assertEqual(backend.machine.uc.reg_read(UC_MIPS_REG_V0), 7)
                backend.access(RESET, 0, 0, 0, 0)
                self.assertEqual(second[0x100:0x104], b'BRI!')
            finally:
                backend.close()
                gc.collect()
                second.close()
                first.close()

    def test_truncated_frame_is_not_silently_accepted(self):
        first, second = socket.socketpair()
        with first, second:
            second.sendall(b'BRI')
            second.shutdown(socket.SHUT_WR)
            with self.assertRaises(ConnectionError):
                receive_exact(first, REQUEST.size)


if __name__ == '__main__':
    unittest.main()
